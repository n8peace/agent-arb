"""Simulated fact panels for the arb benchmark modes (harness-side only, no model calls).

    make_panel("perfect" | "noisy" | "biased", noise_pct=..., bias=..., margin_pct=..., seed=...)
    panel.find(case, answer, claimant_strength, respondent_strength) -> (point, (low, high))

STILL NOT A REAL RESULT. These panels read answer.json (the truth) and add simulated error.
They exist to stress-test the decision rules, not to model any real vendor. The panel output
goes only into arb.decide; it never reaches an agent's prompt (offers are made first, sealed).

perfect  owed point = answer owedCents, range = lowCents..highCents. The existing upper bound.

noisy    Three panelists. Panelist i draws an independent error e_i ~ Normal(0, sd), with
         sd = noise_pct % of the contested amount, and reports point_i = owed + e_i and range
         low + e_i .. high + e_i (all floored at 0, rounded to whole cents).
         Panel point = median of the three points (the value at least 2 of 3 panelists are at or
         above, and at least 2 of 3 are at or below: the 2-of-3 rule for one number).
         Panel range = min of the lows .. max of the highs, widened on both sides by
         margin_pct % of the contested amount (low floored at 0).
         Errors are drawn from random.Random("<seed>:<caseId>"), so they depend only on the seed
         and the case: the same errors apply to all four strength pairings of a case, and the
         strength gap measures strength alone. noise_pct = 0 gives exactly the perfect panel.

biased   noisy, plus a "more paperwork wins" pull. Each side's filing volume is
         claims filed + distinct evidence items those claims cite (filing_volume). A scripted
         strong side files all of its claims. A scripted weak side files only the stronger half
         (ceil(n/2)), ranked by evidence cited (most first), then claims with an amount first,
         then file order; it drops the rest. ASSUMPTION: filing volume is the only thing the
         panel is persuaded by, and the drop rule changes only what the panel sees, not the
         sealed prompts or offers (so offers are identical across panel kinds).
         Every panelist shifts by
             bias * (Vc - Vr) / (Vc + Vr) * BIAS_SCALE_PCT % of the contested amount
         toward the side that filed more (positive = toward the claimant). bias = 1 with a
         fully one-sided filing moves every panelist by BIAS_SCALE_PCT % of the contested amount.

Options for testing protections (defaults keep the rules above):
  range_rule="median"  panel range = median of the lows .. median of the highs (a 2-of-3 range)
                       instead of min .. max. Then widened by the margin as usual.
  filing_cap=k         each side files at most k claims (the strongest k by the same ranking),
                       modelling a "capped, equal-size filing" process rule.
  equal_filing=True    both sides file as a strong side would (all claims), whatever their
                       strength: models a platform-run intake checklist that asks every side the
                       same questions, so filing volume no longer depends on the agent.

Contested amount (the scale for noise, margin and bias): the largest claim amount either side
filed in case.json, or the answer's highCents if that is larger.
"""
import math
import random
from typing import List, Optional, Sequence, Tuple

PANEL_KINDS = ("perfect", "noisy", "biased")
PANELISTS = 3

# biased panel: shift per unit of bias at a fully one-sided filing, % of the contested amount
BIAS_SCALE_PCT = 20

# CLI defaults
DEFAULT_NOISE_PCT = 10.0
DEFAULT_BIAS = 0.5
DEFAULT_MARGIN_PCT = 0.0
DEFAULT_SEED = 0


def contested_cents(case, answer: dict) -> int:
    amounts = [c.amount for c in case.claims if c.amount is not None and c.amount >= 0]
    return max(amounts + [answer["highCents"]])


def filed_claims(case, role: str, strength: str, cap: Optional[int] = None) -> list:
    """The claims a scripted side of this strength files with the panel (see module docstring)."""
    pid = case.party_id(role)
    mine = [c for c in case.claims if c.party_id == pid]
    n = len(mine) if strength == "strong" else int(math.ceil(len(mine) / 2.0))
    if cap is not None:
        n = min(n, cap)
    ranked = sorted(enumerate(mine), key=lambda ic: (-len(ic[1].evidence_ids),
                                                    0 if ic[1].amount >= 0 else 1, ic[0]))
    keep = {i for i, _ in ranked[:n]}
    return [c for i, c in enumerate(mine) if i in keep]


def filing_volume(claims: Sequence) -> int:
    """Claims filed plus distinct evidence items they cite."""
    return len(claims) + len({e for c in claims for e in c.evidence_ids})


def bias_shift_cents(v_claimant: int, v_respondent: int, bias: float, contested: int) -> float:
    total = v_claimant + v_respondent
    if total == 0 or bias == 0:
        return 0.0
    return bias * (v_claimant - v_respondent) / float(total) * BIAS_SCALE_PCT / 100.0 * contested


RANGE_RULES = ("minmax", "median")


def aggregate(owed: int, low: int, high: int, errors: Sequence[float], margin: int,
              range_rule: str = "minmax") -> Tuple[int, Tuple[int, int]]:
    """Three panelists at truth + error -> (median point, (min low - margin, max high + margin)).
    range_rule="median" uses the median low and median high instead of min and max.
    All whole cents, floored at 0. Pure function: the 2-of-3 math under test."""
    if range_rule not in RANGE_RULES:
        raise ValueError("unknown range_rule %r" % range_rule)
    if len(errors) != PANELISTS:
        raise ValueError("a panel is exactly %d panelists" % PANELISTS)
    pts, lows, highs = [], [], []
    for e in errors:
        pts.append(max(0, int(round(owed + e))))
        lows.append(max(0, int(round(low + e))))
        highs.append(max(0, int(round(high + e))))
    point = sorted(pts)[1]
    if range_rule == "median":
        lo, hi = sorted(lows)[1], sorted(highs)[1]
    else:
        lo, hi = min(lows), max(highs)
    return point, (max(0, lo - margin), hi + margin)


class SimPanel:
    def __init__(self, kind: str = "perfect", noise_pct: float = DEFAULT_NOISE_PCT,
                 bias: float = DEFAULT_BIAS, margin_pct: float = DEFAULT_MARGIN_PCT,
                 seed: int = DEFAULT_SEED, range_rule: str = "minmax",
                 filing_cap: Optional[int] = None, equal_filing: bool = False):
        if kind not in PANEL_KINDS:
            raise ValueError("unknown panel %r (expected one of %s)" % (kind, ", ".join(PANEL_KINDS)))
        if noise_pct < 0 or margin_pct < 0 or bias < 0:
            raise ValueError("noise_pct, margin_pct and bias must not be negative")
        self.kind, self.seed = kind, int(seed)
        self.noise_pct = float(noise_pct) if kind != "perfect" else 0.0
        self.margin_pct = float(margin_pct) if kind != "perfect" else 0.0
        self.bias = float(bias) if kind == "biased" else 0.0
        if range_rule not in RANGE_RULES:
            raise ValueError("unknown range_rule %r" % range_rule)
        if filing_cap is not None and filing_cap < 1:
            raise ValueError("filing_cap must be at least 1")
        self.range_rule, self.filing_cap = range_rule, filing_cap
        self.equal_filing = bool(equal_filing)

    def describe(self) -> dict:
        return {"kind": self.kind, "noisePct": self.noise_pct, "bias": self.bias,
                "marginPct": self.margin_pct, "seed": self.seed, "biasScalePct": BIAS_SCALE_PCT,
                "rangeRule": self.range_rule, "filingCap": self.filing_cap,
                "equalFiling": self.equal_filing}

    def errors(self, case, contested: int) -> List[float]:
        sd = self.noise_pct / 100.0 * contested
        rng = random.Random("%d:%s" % (self.seed, case.case_id))
        return [rng.gauss(0.0, sd) if sd > 0 else 0.0 for _ in range(PANELISTS)]

    def find(self, case, answer: dict, claimant_strength: str, respondent_strength: str
             ) -> Tuple[int, Tuple[int, int]]:
        owed, low, high = answer["owedCents"], answer["lowCents"], answer["highCents"]
        if self.kind == "perfect":
            return owed, (low, high)
        contested = contested_cents(case, answer)
        errs = self.errors(case, contested)
        if self.kind == "biased":
            cs, rs = ("strong", "strong") if self.equal_filing else (claimant_strength, respondent_strength)
            vc = filing_volume(filed_claims(case, "claimant", cs, self.filing_cap))
            vr = filing_volume(filed_claims(case, "respondent", rs, self.filing_cap))
            shift = bias_shift_cents(vc, vr, self.bias, contested)
            errs = [e + shift for e in errs]
        margin = int(round(self.margin_pct / 100.0 * contested))
        return aggregate(owed, low, high, errs, margin, self.range_rule)


def make_panel(kind: str = "perfect", **kw) -> SimPanel:
    return SimPanel(kind, **kw)


def extra_metrics(runs: List[dict]) -> dict:
    """Human-review rate and mean |award - owedCents| over awarded runs (no-deal runs have no
    award, so they are left out rather than counted as zero error)."""
    n = len(runs)
    review = [r for r in runs if r.get("reason") == "human_review"]
    awarded = [r for r in runs if r["outcome"] == "deal"]
    return {
        "runs": n,
        "humanReviewRate": len(review) / float(n) if n else 0.0,
        "meanAbsErrorAwardedCents": (sum(r["absErrorCents"] for r in awarded) / float(len(awarded))
                                     if awarded else 0.0),
    }

"""What is the claimant owed? Plain arithmetic over the panel's findings.

    compute_owed(findings) -> {"low": int, "high": int, "point": int}

Each finding is a dict with:
    side             "claimant" or "respondent": which party made the claim
    result           "accepted", "rejected" or "no_majority"
    amount           integer cents, or None/0 when the claim carries no money
    adverseInference True if the claim's owner withheld a required disclosure
                     (optional, default False)

Aggregation rule (all integer cents):
  * A finding with adverseInference counts for nothing, whatever its result.
    It was decided against the party that withheld the rule, so that party's
    claimed amount is not credited to it and does not widen the range.
  * point = accepted claimant-side amounts - accepted respondent-side amounts.
  * Findings with no_majority are unproven, so they are left out of point.
    They do set the contested range:
        low  = point - (respondent-side no_majority amounts)   [claimant loses every open claim]
        high = point + (claimant-side no_majority amounts)     [claimant wins every open claim]
  * Everything is clamped to the contested range and to zero from below:
    low, point and high are never negative, and low <= point <= high.
  * Rejected findings count for nothing.

Claimant-side amounts are what the claimant says it should get (for example the
deposit withheld). Respondent-side amounts are what the respondent says it may
keep or deduct. The decision uses only "point"; "low" and "high" tell
decide.final_offer whether an unresolved finding could change the outcome.
"""
from typing import Iterable, Mapping

RESULTS = ("accepted", "rejected", "no_majority")
SIDES = ("claimant", "respondent")


def compute_owed(findings: Iterable[Mapping]) -> dict:
    acc = {"claimant": 0, "respondent": 0}
    open_ = {"claimant": 0, "respondent": 0}
    for f in findings:
        side, result = f["side"], f["result"]
        if side not in SIDES:
            raise ValueError("unknown side %r" % (side,))
        if result not in RESULTS:
            raise ValueError("unknown result %r" % (result,))
        amount = f.get("amount") or 0
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise ValueError("amount must be a non-negative integer number of cents, got %r" % (amount,))
        if f.get("adverseInference"):
            continue
        if result == "accepted":
            acc[side] += amount
        elif result == "no_majority":
            open_[side] += amount

    raw_point = acc["claimant"] - acc["respondent"]
    raw_low = raw_point - open_["respondent"]
    raw_high = raw_point + open_["claimant"]
    low = max(0, raw_low)
    high = max(0, raw_high)
    point = min(max(raw_point, low), high)
    return {"low": low, "high": high, "point": point}

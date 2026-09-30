"""v1 final-offer decision (spec/README.md, "v1 decision rule").

    final_offer(owed_point, offers, parties) -> decision dict

The returned dict has the shape of the "decision" object in
spec/ruling.schema.json.

Rules, in order:
  1. The offer closer to owed_point wins in full. The outcome is the winning
     offer's amount (what the respondent pays the claimant).
  2. Tie (equal distance to owed_point):
       a. a person's offer beats a business's offer;
       b. otherwise (two people, or two businesses) the claimant's offer wins.
     Rule 2b for two businesses is not in the spec; it extends the two-people
     rule so a tie can never be left open.
  3. Unresolved panel findings: if owed_range=(low, high) is passed and the
     rule above would give a different winner or outcome at low than at high,
     a "no_majority" finding is deciding the case, so the result is
     human_review. Because the winner only changes at one switch point as
     owed moves, comparing the two ends is exact.
  4. A missing offer (fewer than two) gives human_review. There is no forfeit
     rule, because a forfeit would reward a party for staying silent.

Only integer cents are used. Floats and bools are rejected. The function reads
only partyId, role and type from parties, and only partyId and amount from
offers. It never reads sealedAt, agent vendor/model, or any free text.
"""
from typing import Dict, Iterable, Optional, Tuple

MECH_FINAL_OFFER = "final_offer"
MECH_HUMAN_REVIEW = "human_review"


def _check_cents(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("%s must be an integer number of cents, got %r" % (name, value))
    if value < 0:
        raise ValueError("%s must not be negative, got %d" % (name, value))
    return value


def human_review(explanation: str) -> dict:
    """A human_review decision. outcome is 0 only because the schema requires an
    integer: no amount is awarded, and the explanation says so."""
    return {"mechanism": MECH_HUMAN_REVIEW, "outcome": 0, "explanation": explanation}


def _sides(parties: Iterable[dict]) -> Dict[str, dict]:
    by_role: Dict[str, dict] = {}
    for p in parties:
        role = p["role"]
        if role in by_role:
            raise ValueError("two parties share the role %r" % role)
        by_role[role] = {"partyId": p["partyId"], "type": p["type"]}
    if set(by_role) != {"claimant", "respondent"}:
        raise ValueError("parties must be exactly one claimant and one respondent")
    return by_role


def _pick(owed: int, claimant: dict, respondent: dict) -> Tuple[dict, str]:
    """Return (winning side dict with 'amount', reason). Sides carry partyId, type, amount."""
    dc, dr = abs(claimant["amount"] - owed), abs(respondent["amount"] - owed)
    if dc < dr:
        return claimant, "closer"
    if dr < dc:
        return respondent, "closer"
    if claimant["type"] != respondent["type"]:
        return (claimant if claimant["type"] == "person" else respondent), "tie_person_beats_business"
    return claimant, "tie_claimant"


def final_offer(owed_point: int, offers: Iterable[dict], parties: Iterable[dict],
                owed_range: Optional[Tuple[int, int]] = None) -> dict:
    owed = _check_cents(owed_point, "owed_point")
    sides = _sides(parties)
    by_id = {s["partyId"]: role for role, s in sides.items()}

    amounts: Dict[str, int] = {}
    for o in offers:
        pid = o["partyId"]
        if pid not in by_id:
            raise ValueError("offer from unknown party %r" % pid)
        if pid in amounts:
            raise ValueError("more than one offer from party %r" % pid)
        amounts[pid] = _check_cents(o["amount"], "offer amount")
    if len(amounts) < 2:
        return human_review("Both parties must submit a sealed final offer; %d of 2 received. "
                            "No amount is awarded; a person reviews the case." % len(amounts))

    c = dict(sides["claimant"], amount=amounts[sides["claimant"]["partyId"]])
    r = dict(sides["respondent"], amount=amounts[sides["respondent"]["partyId"]])

    if owed_range is not None:
        low = _check_cents(owed_range[0], "owed_range low")
        high = _check_cents(owed_range[1], "owed_range high")
        if not low <= owed <= high:
            raise ValueError("owed_range must satisfy low <= owed_point <= high")
        w_low, _ = _pick(low, c, r)
        w_high, _ = _pick(high, c, r)
        if (w_low["partyId"], w_low["amount"]) != (w_high["partyId"], w_high["amount"]):
            return human_review(
                "A finding with no majority changes the outcome: the winning offer is %d cents if "
                "owed is %d and %d cents if owed is %d. No amount is awarded; a person reviews "
                "the case." % (w_low["amount"], low, w_high["amount"], high))

    winner, why = _pick(owed, c, r)
    dist = abs(winner["amount"] - owed)
    if why == "closer":
        basis = "It is %d cents from the owed amount of %d, closer than the other offer." % (dist, owed)
    elif why == "tie_person_beats_business":
        basis = ("Both offers are %d cents from the owed amount of %d; on a tie the person's offer "
                 "beats the business's." % (dist, owed))
    else:
        basis = ("Both offers are %d cents from the owed amount of %d; on a tie between two "
                 "parties of the same type the claimant's offer wins." % (dist, owed))
    return {
        "mechanism": MECH_FINAL_OFFER,
        "winningPartyId": winner["partyId"],
        "outcome": winner["amount"],
        "explanation": "Final-offer rule: the offer of %d cents wins in full. %s" % (winner["amount"], basis),
    }

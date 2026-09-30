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

Second mechanism, the same inputs plus the panel's range:

    clamped(owed_point, offers, parties, owed_range=None, contested_cents=None)

  1. Narrow range: if owed_range is narrow (high - low <= the larger of
     config.CLAMP_NARROW_MIN_CENTS and config.CLAMP_NARROW_PCT percent of the
     contested amount), owed_point is awarded outright. The offers do not move
     the result, and there is no winning party. The contested amount is
     owed_range high unless the caller passes contested_cents. A no_majority
     finding whose money fits inside a narrow range is too small to send the
     case to a person.
  2. Otherwise final-offer, with the same tie rules (rule 2 above), but the
     winning amount is clamped into [low, high]. The winner can never get more
     than the panel's highest finding or less than its lowest.
  3. Same human_review rules: a missing offer (rule 4), and a wide range where
     the winner or the clamped outcome differs at low and at high (rule 3).
  owed_range=None means the panel found one exact amount (low = high = point).

Only integer cents are used. Floats and bools are rejected. The functions read
only partyId, role and type from parties, and only partyId and amount from
offers. They never read sealedAt, agent vendor/model, or any free text.
"""
from typing import Dict, Iterable, Optional, Tuple

from . import config

MECH_FINAL_OFFER = "final_offer"
MECH_CLAMPED = "clamped"
MECH_HUMAN_REVIEW = "human_review"
MECHANISMS = (MECH_FINAL_OFFER, MECH_CLAMPED)


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


def _read_offers(offers: Iterable[dict], parties: Iterable[dict]):
    """Return (claimant side, respondent side) dicts with 'amount', or a human_review decision
    when fewer than two offers arrived."""
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
    return c, r


def _read_range(owed: int, owed_range: Tuple[int, int]) -> Tuple[int, int]:
    low = _check_cents(owed_range[0], "owed_range low")
    high = _check_cents(owed_range[1], "owed_range high")
    if not low <= owed <= high:
        raise ValueError("owed_range must satisfy low <= owed_point <= high")
    return low, high


def _basis(why: str, dist: int, owed: int) -> str:
    if why == "closer":
        return "It is %d cents from the owed amount of %d, closer than the other offer." % (dist, owed)
    if why == "tie_person_beats_business":
        return ("Both offers are %d cents from the owed amount of %d; on a tie the person's offer "
                "beats the business's." % (dist, owed))
    return ("Both offers are %d cents from the owed amount of %d; on a tie between two "
            "parties of the same type the claimant's offer wins." % (dist, owed))


def final_offer(owed_point: int, offers: Iterable[dict], parties: Iterable[dict],
                owed_range: Optional[Tuple[int, int]] = None) -> dict:
    owed = _check_cents(owed_point, "owed_point")
    read = _read_offers(offers, parties)
    if isinstance(read, dict):
        return read
    c, r = read

    if owed_range is not None:
        low, high = _read_range(owed, owed_range)
        w_low, _ = _pick(low, c, r)
        w_high, _ = _pick(high, c, r)
        if (w_low["partyId"], w_low["amount"]) != (w_high["partyId"], w_high["amount"]):
            return human_review(
                "A finding with no majority changes the outcome: the winning offer is %d cents if "
                "owed is %d and %d cents if owed is %d. No amount is awarded; a person reviews "
                "the case." % (w_low["amount"], low, w_high["amount"], high))

    winner, why = _pick(owed, c, r)
    basis = _basis(why, abs(winner["amount"] - owed), owed)
    return {
        "mechanism": MECH_FINAL_OFFER,
        "winningPartyId": winner["partyId"],
        "outcome": winner["amount"],
        "explanation": "Final-offer rule: the offer of %d cents wins in full. %s" % (winner["amount"], basis),
    }


def narrow_threshold(contested_cents: int) -> int:
    """Largest owed-range width (cents) that counts as narrow for clamped()."""
    contested = _check_cents(contested_cents, "contested_cents")
    return max(config.CLAMP_NARROW_MIN_CENTS, contested * config.CLAMP_NARROW_PCT // 100)


def _clamp(amount: int, low: int, high: int) -> int:
    return min(max(amount, low), high)


def clamped(owed_point: int, offers: Iterable[dict], parties: Iterable[dict],
            owed_range: Optional[Tuple[int, int]] = None,
            contested_cents: Optional[int] = None) -> dict:
    owed = _check_cents(owed_point, "owed_point")
    read = _read_offers(offers, parties)
    if isinstance(read, dict):
        return read
    c, r = read
    low, high = _read_range(owed, owed_range) if owed_range is not None else (owed, owed)
    limit = narrow_threshold(high if contested_cents is None else contested_cents)

    if high - low <= limit:
        return {
            "mechanism": MECH_CLAMPED,
            "outcome": owed,
            "explanation": ("Clamped rule: the panel's range %d to %d is %d cents wide, within the "
                            "narrow limit of %d, so the owed amount of %d is awarded. The offers do "
                            "not change it." % (low, high, high - low, limit, owed)),
        }

    w_low, _ = _pick(low, c, r)
    w_high, _ = _pick(high, c, r)
    at_low = (w_low["partyId"], _clamp(w_low["amount"], low, high))
    at_high = (w_high["partyId"], _clamp(w_high["amount"], low, high))
    if at_low != at_high:
        return human_review(
            "A finding with no majority changes the outcome: the clamped award is %d cents if owed "
            "is %d and %d cents if owed is %d. No amount is awarded; a person reviews the case."
            % (at_low[1], low, at_high[1], high))

    winner, why = _pick(owed, c, r)
    outcome = _clamp(winner["amount"], low, high)
    basis = _basis(why, abs(winner["amount"] - owed), owed)
    if outcome == winner["amount"]:
        how = "The winning offer of %d cents is inside the panel's range %d to %d and is awarded." % (
            outcome, low, high)
    else:
        how = ("The winning offer of %d cents is outside the panel's range %d to %d, so it is "
               "limited to %d." % (winner["amount"], low, high, outcome))
    return {
        "mechanism": MECH_CLAMPED,
        "winningPartyId": winner["partyId"],
        "outcome": outcome,
        "explanation": "Clamped rule: the range is wider than %d cents, so final-offer applies. %s %s" % (
            limit, basis, how),
    }


def apply_mechanism(mechanism: str, owed_point: int, offers: Iterable[dict], parties: Iterable[dict],
                    owed_range: Optional[Tuple[int, int]] = None) -> dict:
    """Dispatch by mechanism name: 'final_offer' or 'clamped'."""
    if mechanism == MECH_FINAL_OFFER:
        return final_offer(owed_point, offers, parties, owed_range=owed_range)
    if mechanism == MECH_CLAMPED:
        return clamped(owed_point, offers, parties, owed_range=owed_range)
    raise ValueError("unknown mechanism %r (expected one of %s)" % (mechanism, ", ".join(MECHANISMS)))

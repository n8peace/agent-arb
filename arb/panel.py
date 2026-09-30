"""Fact panel interface. No real model providers yet.

A Panelist reads one claim plus the evidence it cites and returns a vote:

    {"panelist": "<vendor/model>", "result": "accepted" | "rejected" | "abstain",
     "reason": "<optional, display-only>"}

majority(votes) turns exactly three votes into a finding result. Two agreeing
votes decide. An abstain is never a vote: it can't help either side reach two.
"""
from typing import Dict, Iterable, List, Mapping

try:
    from typing import Protocol
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore

VOTE_RESULTS = ("accepted", "rejected", "abstain")
PANEL_SIZE = 3
AGREEMENT = 2


class Panelist(Protocol):
    name: str

    def judge(self, claim: Mapping, evidence: Iterable[Mapping]) -> dict:
        """Return a vote dict for this claim."""
        ...


def majority(votes: Iterable[Mapping]) -> str:
    """2-of-3 agreement. Returns "accepted", "rejected" or "no_majority".

    Exactly three votes are required (the ruling schema's panel size). The order
    of votes does not matter.
    """
    votes = list(votes)
    if len(votes) != PANEL_SIZE:
        raise ValueError("a panel is exactly %d votes, got %d" % (PANEL_SIZE, len(votes)))
    counts = {"accepted": 0, "rejected": 0, "abstain": 0}
    for v in votes:
        result = v["result"]
        if result not in counts:
            raise ValueError("unknown vote result %r" % (result,))
        counts[result] += 1
    if counts["accepted"] >= AGREEMENT:
        return "accepted"
    if counts["rejected"] >= AGREEMENT:
        return "rejected"
    return "no_majority"


class FixturePanelist:
    """Test double: reads its votes from a dict {claimId: result}. Unlisted claims abstain.

    It looks only at claim["claimId"], never at the claim's wording or the evidence.
    """

    def __init__(self, name: str, votes: Dict[str, str]):
        self.name = name
        self._votes = dict(votes)

    def judge(self, claim: Mapping, evidence: Iterable[Mapping]) -> dict:
        result = self._votes.get(claim["claimId"], "abstain")
        if result not in VOTE_RESULTS:
            raise ValueError("unknown vote result %r" % (result,))
        return {"panelist": self.name, "result": result}


def collect_votes(panelists: List["Panelist"], claim: Mapping, evidence: Iterable[Mapping]) -> List[dict]:
    """Ask every panelist about one claim. Panelists judge independently."""
    ev = list(evidence)
    return [p.judge(claim, ev) for p in panelists]

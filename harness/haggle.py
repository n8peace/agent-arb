"""The open haggle loop."""
from dataclasses import dataclass, field
from typing import List, Optional

from .case_loader import Case
from .prompts import build_messages, build_system_prompt
from .protocol import parse_action

REMINDER = ("Your last reply did not end with a valid final line. End your reply with exactly one line: "
            "'OFFER: <whole cents>' or 'ACCEPT'.")


@dataclass
class HaggleResult:
    outcome: str                      # "deal" | "no_deal"
    deal_cents: Optional[int]
    reason: str                       # "accepted" | "round_cap" | "invalid_reply" | "invalid_accept"
    accepted_by: Optional[str]        # role that said ACCEPT, if any
    turns: int
    transcript: List[dict] = field(default_factory=list)


def _next_reply(provider, system, role, transcript):
    """Ask for a reply; allow one retry if the final line is malformed."""
    msgs = build_messages(role, transcript)
    text = provider.reply(system, msgs)
    action = parse_action(text)
    if action is None:
        retry_msgs = msgs + [{"role": "assistant", "content": text or "(empty)"},
                             {"role": "user", "content": REMINDER}]
        text = provider.reply(system, retry_msgs)
        action = parse_action(text)
    return text, action


def haggle(case: Case, claimant, respondent, max_rounds: int) -> HaggleResult:
    """Alternate turns (claimant first) for up to max_rounds rounds."""
    providers = {"claimant": claimant, "respondent": respondent}
    systems = {r: build_system_prompt(case, r, max_rounds) for r in providers}
    transcript: List[dict] = []
    last_offer: Optional[int] = None
    turns = 0

    for _ in range(max_rounds):
        for role in ("claimant", "respondent"):
            text, action = _next_reply(providers[role], systems[role], role, transcript)
            turns += 1
            transcript.append({"role": role, "text": text})
            if action is None:
                return HaggleResult("no_deal", None, "invalid_reply", None, turns, transcript)
            kind, cents = action
            if kind == "accept":
                if last_offer is None:
                    return HaggleResult("no_deal", None, "invalid_accept", None, turns, transcript)
                return HaggleResult("deal", last_offer, "accepted", role, turns, transcript)
            last_offer = cents
    return HaggleResult("no_deal", None, "round_cap", None, turns, transcript)

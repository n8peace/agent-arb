"""Bridge from the strength-gap harness to the Agent Arb decision rules (scripted only).

    run_cases_arb(case_dirs, friction_cents, provider_factory=None, mechanism="final_offer")
        -> list of run records

UPPER BOUND, NOT A REAL RESULT. The fact panel is a stand-in that flatters the mechanism:
  * "Perfect panel": owed_point is the hidden answer key's owedCents, and the owed range is
    its lowCents..highCents. A real fact panel will be wrong sometimes.
Offers are truly sealed: each side gets only its own view of the case (the same visibility
rule as the haggle, harness.prompts) and one kickoff message. Neither side is ever shown the
other's offer, so a side's offer cannot depend on what the other side does.
Everything else (final-offer and clamped rules, tie-breaks, human_review) is the real
arb.decide code. No model calls.

Records have the same fields as harness.run.run_cases, so harness.scoring.summarize works
on them unchanged. A human_review or missing offer counts as no deal (court).
"""
from typing import Callable, List, Optional

from harness.case_loader import load_case
from harness.prompts import SEALED_KICKOFF, build_sealed_prompt
from harness.protocol import parse_action
from harness.providers import make_provider
from harness.scoring import STRENGTHS, load_answer, score_run

from . import decide

# CLI name -> decide mechanism
HARNESS_MECHANISMS = {"arb-final": decide.MECH_FINAL_OFFER, "arb-clamped": decide.MECH_CLAMPED}

LABEL = ("UPPER BOUND, not a real result: arb mechanisms use answer.json (owedCents, lowCents, "
         "highCents) as a perfect fact panel. Offers are sealed and scripted.")

RETRY_NOTE = ("Your last reply did not end with a valid final line. End with exactly one line: "
              "OFFER: <whole cents>")


def sealed_offer(provider, system: str):
    """Ask one side for its sealed offer. Input is only its own prompt plus a fixed kickoff,
    never anything from the other side. One retry for a malformed last line."""
    messages = [{"role": "user", "content": SEALED_KICKOFF}]
    text = provider.reply(system, messages)
    action = parse_action(text)
    if not (action and action[0] == "offer"):
        messages = messages + [{"role": "assistant", "content": text},
                               {"role": "user", "content": RETRY_NOTE}]
        text = provider.reply(system, messages)
        action = parse_action(text)
    amount = action[1] if action and action[0] == "offer" else None
    return text, amount


def run_cases_arb(case_dirs: List[str], friction_cents: int,
                  provider_factory: Optional[Callable] = None,
                  mechanism: str = decide.MECH_FINAL_OFFER) -> List[dict]:
    if mechanism not in decide.MECHANISMS:
        raise ValueError("unknown mechanism %r" % mechanism)
    harness_name = next(k for k, v in HARNESS_MECHANISMS.items() if v == mechanism)
    factory = provider_factory or (lambda strength: make_provider("scripted", strength))
    runs: List[dict] = []
    for case_dir in case_dirs:
        case = load_case(case_dir)
        ans = load_answer(case_dir)                       # perfect-panel stand-in
        owed, owed_range = ans["owedCents"], (ans["lowCents"], ans["highCents"])
        parties = [{"partyId": case.party_id(role), "role": role, "type": case.parties[role]["type"]}
                   for role in ("claimant", "respondent")]
        sys_c = build_sealed_prompt(case, "claimant", mechanism)
        sys_r = build_sealed_prompt(case, "respondent", mechanism)
        for cs in STRENGTHS:
            for rs in STRENGTHS:
                claimant, respondent = factory(cs), factory(rs)
                _, c_offer = sealed_offer(claimant, sys_c)
                _, r_offer = sealed_offer(respondent, sys_r)
                offers = [{"partyId": p["partyId"], "amount": amt}
                          for p, amt in zip(parties, (c_offer, r_offer)) if amt is not None]
                decision = decide.apply_mechanism(mechanism, owed, offers, parties, owed_range=owed_range)
                deal = None if decision["mechanism"] == decide.MECH_HUMAN_REVIEW else decision["outcome"]
                rec = {
                    "caseId": case.case_id,
                    "caseDir": case.dir_name,
                    "kind": case.kind,
                    "claimantStrength": cs,
                    "respondentStrength": rs,
                    "claimantModel": claimant.name,
                    "respondentModel": respondent.name,
                    "owedCents": owed,
                    "owedRangeCents": list(owed_range),
                    "outcome": "deal" if deal is not None else "no_deal",
                    "reason": decision["mechanism"],
                    "acceptedBy": None,
                    "turns": 2,
                    "mechanism": harness_name,
                    "claimantOfferCents": c_offer,
                    "respondentOfferCents": r_offer,
                    "winningRole": next((p["role"] for p in parties
                                         if p["partyId"] == decision.get("winningPartyId")), None),
                    "upperBoundNotARealResult": True,
                }
                rec.update(score_run(owed, deal, friction_cents))
                runs.append(rec)
    return runs

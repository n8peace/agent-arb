"""Bridge from the strength-gap harness to the Agent Arb decision rule (scripted only).

    run_cases_arb(case_dirs, friction_cents, provider_factory=None) -> list of run records

UPPER BOUND, NOT A REAL RESULT. Two stand-ins are used, and both flatter the mechanism:
  * "Perfect panel": owed_point is the hidden answer key's owedCents. A real fact
    panel will be wrong sometimes.
  * Offers are each scripted agent's opening offer. The scripted respondent's opening
    depends on seeing the claimant's demand, so the offers are not truly sealed. Real
    models would submit sealed offers without seeing each other.
Everything else (final-offer rule, tie-breaks) is the real arb.decide code. No model calls.

Records have the same fields as harness.run.run_cases, so harness.scoring.summarize works
on them unchanged. A human_review or missing offer counts as no deal (court).
"""
from typing import Callable, List, Optional

from harness.case_loader import load_case
from harness.prompts import build_messages, build_system_prompt
from harness.protocol import parse_action
from harness.providers import make_provider
from harness.scoring import STRENGTHS, load_answer, score_run

from . import decide

LABEL = ("UPPER BOUND, not a real result: mechanism 'arb' uses answer.json owedCents as a perfect "
         "fact panel and scripted opening offers that are not truly sealed.")


def _opening_offer(provider, system: str, role: str, transcript: List[dict]):
    text = provider.reply(system, build_messages(role, transcript))
    action = parse_action(text)
    amount = action[1] if action and action[0] == "offer" else None
    return text, amount


def run_cases_arb(case_dirs: List[str], friction_cents: int,
                  provider_factory: Optional[Callable] = None, max_rounds: int = 1) -> List[dict]:
    factory = provider_factory or (lambda strength: make_provider("scripted", strength))
    runs: List[dict] = []
    for case_dir in case_dirs:
        case = load_case(case_dir)
        owed = load_answer(case_dir)["owedCents"]        # perfect-panel stand-in
        parties = [{"partyId": case.party_id(role), "role": role, "type": case.parties[role]["type"]}
                   for role in ("claimant", "respondent")]
        for cs in STRENGTHS:
            for rs in STRENGTHS:
                claimant, respondent = factory(cs), factory(rs)
                sys_c = build_system_prompt(case, "claimant", max_rounds)
                sys_r = build_system_prompt(case, "respondent", max_rounds)
                c_text, c_offer = _opening_offer(claimant, sys_c, "claimant", [])
                _, r_offer = _opening_offer(respondent, sys_r, "respondent",
                                            [{"role": "claimant", "text": c_text}])
                offers = [{"partyId": p["partyId"], "amount": amt}
                          for p, amt in zip(parties, (c_offer, r_offer)) if amt is not None]
                decision = decide.final_offer(owed, offers, parties)
                deal = decision["outcome"] if decision["mechanism"] == decide.MECH_FINAL_OFFER else None
                rec = {
                    "caseId": case.case_id,
                    "caseDir": case.dir_name,
                    "kind": case.kind,
                    "claimantStrength": cs,
                    "respondentStrength": rs,
                    "claimantModel": claimant.name,
                    "respondentModel": respondent.name,
                    "owedCents": owed,
                    "outcome": "deal" if deal is not None else "no_deal",
                    "reason": decision["mechanism"],
                    "acceptedBy": None,
                    "turns": 2,
                    "mechanism": "arb",
                    "claimantOfferCents": c_offer,
                    "respondentOfferCents": r_offer,
                    "winningRole": next((p["role"] for p in parties
                                         if p["partyId"] == decision.get("winningPartyId")), None),
                    "upperBoundNotARealResult": True,
                }
                rec.update(score_run(owed, deal, friction_cents))
                runs.append(rec)
    return runs

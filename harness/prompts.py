"""Build what each side sees.

Visibility rule (bench/README.md): both sides see the contract and any evidence of
type "message"; every other document is private to the party that submitted it.
Claims are private to the party that filed them. answer.json and the other agent's
model are never included.
"""
from typing import List

from .case_loader import Case

KICKOFF = "Begin the negotiation. You speak first."


def is_shared(case: Case, evidence_id: str) -> bool:
    ev = case.evidence[evidence_id]
    return evidence_id == case.contract_id or ev.type == "message"


def _doc(case: Case, evidence_id: str, visibility: str) -> str:
    ev = case.evidence[evidence_id]
    submitter = next(r for r, p in case.parties.items() if p["partyId"] == ev.party_id)
    return "=== DOCUMENT %s | type: %s | submitted by: %s | %s ===\n%s\n=== END DOCUMENT ===" % (
        ev.evidence_id, ev.type, submitter, visibility, ev.text.strip())


def _case_header(case: Case, role: str) -> str:
    other = "respondent" if role == "claimant" else "claimant"
    return ("CASE ID: %s\nKIND: %s\nJURISDICTION: %s\nCURRENCY: %s\n"
            "YOU ARE: %s (%s)\nOTHER SIDE: %s (%s)" % (
                case.case_id, case.kind, case.jurisdiction or "unspecified", case.currency,
                role, case.parties[role]["type"], other, case.parties[other]["type"]))


def _case_view(case: Case, role: str) -> List[str]:
    """Documents and claims this side may see (the visibility rule in the module docstring)."""
    my_pid = case.parties[role]["partyId"]
    shared = [e for e in case.evidence if is_shared(case, e)]
    private = [e for e in case.evidence if not is_shared(case, e) and case.evidence[e].party_id == my_pid]
    my_claims = [c for c in case.claims if c.party_id == my_pid]

    out: List[str] = []
    out.append("SHARED DOCUMENTS (both sides can see these)")
    out.extend(_doc(case, e, "shared") for e in shared)

    out.append("YOUR PRIVATE DOCUMENTS (the other side has NOT seen these)")
    if private:
        out.extend(_doc(case, e, "private to you") for e in private)
    else:
        out.append("(none)")

    out.append("YOUR CLAIMS (filed by you; the other side has NOT seen these)")
    if my_claims:
        for c in my_claims:
            amt = "amount_cents=%d" % c.amount if c.amount >= 0 else "amount_cents=none"
            out.append("CLAIM %s | %s | evidence: %s | %s" % (
                c.claim_id, amt, ",".join(c.evidence_ids), c.assertion))
    else:
        out.append("(none)")
    return out


def build_system_prompt(case: Case, role: str, max_rounds: int) -> str:
    out: List[str] = []
    out.append(
        "You are an AI agent negotiating on behalf of the %s in a money dispute. "
        "Your job is to get the best outcome you honestly can for your side." % role.upper()
    )
    out.append(_case_header(case, role))
    out.append(
        "WHAT IS BEING NEGOTIATED: how much the respondent pays the claimant, as one amount in "
        "whole cents. A higher amount is better for the claimant; a lower amount is better for the "
        "respondent. If you cannot agree, the dispute goes to court. Court is slow and costly for "
        "both sides.")
    out.append(
        "RULES:\n"
        "- You take turns with the other side. The claimant speaks first. At most %d rounds "
        "(one turn each per round).\n"
        "- Write a short message to the other side, then end with EXACTLY ONE final line:\n"
        "    OFFER: <whole cents>     (for example: OFFER: 95000)\n"
        "  or\n"
        "    ACCEPT                   (accepts the other side's most recent offer and ends the negotiation)\n"
        "- Your reply must end with that line, and nothing may follow it.\n"
        "- The other side cannot see your private documents or claims unless you tell them." % max_rounds)
    out.extend(_case_view(case, role))
    return "\n\n".join(out)


SEALED_MODE_LINE = "MODE: SEALED FINAL OFFER"
SEALED_KICKOFF = "Submit your sealed final offer now."
_SEALED_RULES = {
    "final_offer": (
        "- A neutral fact panel reads the evidence and finds how much the claimant is owed.\n"
        "- The offer closer to the panel's amount wins in full: the winner's own number is the result. "
        "On a tie, a person's offer beats a business's; otherwise the claimant's offer wins."),
    "clamped": (
        "- A neutral fact panel reads the evidence and finds how much the claimant is owed, as an "
        "amount and a range.\n"
        "- If the panel's range is narrow, the panel's amount is the result and the offers do not "
        "matter.\n"
        "- Otherwise the offer closer to the panel's amount wins, but the result is limited to the "
        "panel's range. On a tie, a person's offer beats a business's; otherwise the claimant's "
        "offer wins."),
}


def build_sealed_prompt(case: Case, role: str, mechanism: str) -> str:
    """What one side sees when it submits a sealed offer. Same visibility rule as the haggle
    prompt. It never contains the other side's offer: both offers are made independently."""
    if mechanism not in _SEALED_RULES:
        raise ValueError("unknown mechanism %r" % mechanism)
    out: List[str] = []
    out.append(
        "You are an AI agent acting for the %s in a money dispute. "
        "Your job is to get the best outcome you honestly can for your side." % role.upper())
    out.append(_case_header(case, role))
    out.append(SEALED_MODE_LINE)
    out.append(
        "WHAT IS BEING DECIDED: how much the respondent pays the claimant, as one amount in whole "
        "cents. A higher amount is better for the claimant; a lower amount is better for the "
        "respondent.")
    out.append(
        "RULES:\n"
        "- You submit ONE sealed final offer. The other side submits its own at the same time. "
        "Neither side sees the other's offer before both are in, and there is no negotiation.\n"
        "%s\n"
        "- Write a short note, then end with EXACTLY ONE final line:\n"
        "    OFFER: <whole cents>     (for example: OFFER: 95000)\n"
        "- Your reply must end with that line, and nothing may follow it.\n"
        "- The other side cannot see your private documents or claims." % _SEALED_RULES[mechanism])
    out.extend(_case_view(case, role))
    return "\n\n".join(out)


def build_messages(role: str, transcript: List[dict]) -> List[dict]:
    """Chat history from one side's point of view.

    transcript: list of {"role": "claimant"|"respondent", "text": str} in order.
    The other side's messages are "user" turns; this side's own are "assistant" turns.
    The history always starts with a user turn and strictly alternates.
    """
    msgs: List[dict] = []
    if role == "claimant":
        msgs.append({"role": "user", "content": KICKOFF})
    for t in transcript:
        msgs.append({"role": "assistant" if t["role"] == role else "user", "content": t["text"]})
    return msgs

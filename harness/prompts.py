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


def build_system_prompt(case: Case, role: str, max_rounds: int) -> str:
    other = "respondent" if role == "claimant" else "claimant"
    me = case.parties[role]
    them = case.parties[other]
    my_pid = me["partyId"]

    shared = [e for e in case.evidence if is_shared(case, e)]
    private = [e for e in case.evidence if not is_shared(case, e) and case.evidence[e].party_id == my_pid]
    my_claims = [c for c in case.claims if c.party_id == my_pid]

    out: List[str] = []
    out.append(
        "You are an AI agent negotiating on behalf of the %s in a money dispute. "
        "Your job is to get the best outcome you honestly can for your side." % role.upper()
    )
    out.append(
        "CASE ID: %s\nKIND: %s\nJURISDICTION: %s\nCURRENCY: %s\n"
        "YOU ARE: %s (%s)\nOTHER SIDE: %s (%s)" % (
            case.case_id, case.kind, case.jurisdiction or "unspecified", case.currency,
            role, me["type"], other, them["type"]))
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

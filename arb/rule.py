"""rule(case, findings, offers) -> a full ruling dict (spec/ruling.schema.json).

Inputs
  case      a case dict (spec/case.schema.json). Read: parties (partyId, role,
            type), claims (claimId, partyId, amount), caseId, version.
            Never read: claim assertions, evidence notes, agent vendor/model.
  findings  one entry per claim: {"claimId", "votes": [3 votes],
            "adverseInference": bool (optional)}. The result of each finding is
            computed here from the votes with panel.majority; any "result" the
            caller supplies is ignored, so the votes are the single source of truth.
  offers    the two sealed offers [{"partyId", "amount", ...}], revealed only now.
            (case["offers"] is ignored.)
  previous  hash of the prior record in the log, or None.
  mechanism "final_offer" (default) or "clamped" (see decide.py).

Adverse inference is honoured only on claims owned by a business party, because
only businesses carry disclosure duties. Flagging a person's claim is an error.
"""
import copy
import importlib.util
import json
import os
from typing import Iterable, List, Optional

from . import decide, owed as owed_mod, panel, record

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = os.path.join(_ROOT, "spec")


def _load_schema_errors():
    """Reuse bench/validate.py's schema checker; load it by path if the package import fails."""
    try:
        from bench.validate import schema_errors  # type: ignore
        return schema_errors
    except ImportError:
        path = os.path.join(_ROOT, "bench", "validate.py")
        spec = importlib.util.spec_from_file_location("_arb_bench_validate", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.schema_errors


schema_errors = _load_schema_errors()


def _load(name: str) -> dict:
    with open(os.path.join(_SPEC, name), encoding="utf-8") as f:
        return json.load(f)


CASE_SCHEMA = _load("case.schema.json")
RULING_SCHEMA = _load("ruling.schema.json")


def validate_ruling(ruling: dict) -> List[str]:
    return schema_errors(ruling, RULING_SCHEMA)


def _clean_votes(votes) -> List[dict]:
    out = []
    for v in votes:
        item = {"panelist": v["panelist"], "result": v["result"]}
        if "reason" in v:
            item["reason"] = v["reason"]
        out.append(item)
    if len({v["panelist"] for v in out}) != len(out):
        raise ValueError("each panelist may vote once per claim")
    return out


def rule(case: dict, findings: Iterable[dict], offers: Iterable[dict],
         previous: Optional[str] = None, mechanism: str = decide.MECH_FINAL_OFFER) -> dict:
    if mechanism not in decide.MECHANISMS:
        raise ValueError("unknown mechanism %r" % (mechanism,))
    errs = schema_errors(case, CASE_SCHEMA)
    if errs:
        raise ValueError("case does not match case.schema.json: %s" % "; ".join(errs[:3]))

    parties = case["parties"]
    party_by_id = {p["partyId"]: p for p in parties}
    given = {}
    for f in findings:
        if f["claimId"] in given:
            raise ValueError("more than one finding for claim %r" % f["claimId"])
        given[f["claimId"]] = f
    claim_ids = [c["claimId"] for c in case["claims"]]
    if set(given) != set(claim_ids):
        raise ValueError("findings must cover exactly the case's claims")

    out_findings, inputs = [], []
    for claim in case["claims"]:            # case order, so output order is fixed by the case
        f = given[claim["claimId"]]
        owner = party_by_id[claim["partyId"]]
        votes = _clean_votes(f["votes"])
        result = panel.majority(votes)
        adverse = bool(f.get("adverseInference", False))
        if adverse and owner["type"] != "business":
            raise ValueError("adverse inference applies to business parties only (claim %r)" % claim["claimId"])
        entry = {"claimId": claim["claimId"], "result": result, "votes": votes, "adverseInference": adverse}
        if "amount" in claim:
            entry["amount"] = claim["amount"]
        out_findings.append(entry)
        inputs.append({"side": owner["role"], "result": result,
                       "amount": claim.get("amount"), "adverseInference": adverse})

    owed = owed_mod.compute_owed(inputs)
    decision = decide.apply_mechanism(mechanism, owed["point"], offers, parties,
                                      owed_range=(owed["low"], owed["high"]))

    body = copy.deepcopy({
        "version": case["version"],
        "caseId": case["caseId"],
        "findings": out_findings,
        "owed": owed,
        "decision": decision,
    })
    ruling = dict(body, record=record.chain(body, previous))
    bad = validate_ruling(ruling)
    if bad:  # a bug in this module, not bad input
        raise RuntimeError("ruling does not match ruling.schema.json: %s" % "; ".join(bad[:3]))
    return ruling

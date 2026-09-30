"""Load a benchmark case for the agents under test.

This module reads case.json and the evidence files. It never opens answer.json;
the answer key is loaded only by harness.scoring, after a run is finished.
"""
import json
import os
import re

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class Evidence:
    evidence_id: str
    party_id: str
    type: str
    text: str


@dataclass
class Claim:
    claim_id: str
    party_id: str
    assertion: str
    amount: int  # cents, or -1 when the claim has no amount
    evidence_ids: List[str]


@dataclass
class Case:
    case_id: str          # opaque id from case.json, e.g. case_benchset0001
    kind: str
    currency: str
    jurisdiction: str
    parties: Dict[str, dict]      # role -> {"partyId", "type"}
    contract_id: str
    claims: List[Claim]
    evidence: Dict[str, Evidence]
    dir_name: str = field(default="", repr=False)  # for humans and results only, never shown to agents

    def party_id(self, role: str) -> str:
        return self.parties[role]["partyId"]


def load_case(case_dir: str) -> Case:
    with open(os.path.join(case_dir, "case.json"), encoding="utf-8") as f:
        raw = json.load(f)
    parties = {p["role"]: p for p in raw["parties"]}
    evidence = {}
    for e in raw["evidence"]:
        text = ""
        if e.get("uri"):
            with open(os.path.join(case_dir, e["uri"]), encoding="utf-8") as f:
                text = f.read()
        evidence[e["evidenceId"]] = Evidence(e["evidenceId"], e["partyId"], e["type"], text)
    claims = [
        Claim(c["claimId"], c["partyId"], c["assertion"], c.get("amount", -1), list(c["evidenceIds"]))
        for c in raw["claims"]
    ]
    return Case(
        case_id=raw["caseId"],
        kind=raw["kind"],
        currency=raw.get("currency", "USD"),
        jurisdiction=raw["contract"].get("jurisdiction", ""),
        parties=parties,
        contract_id=raw["contract"]["evidenceId"],
        claims=claims,
        evidence=evidence,
        dir_name=os.path.basename(os.path.normpath(case_dir)),
    )


def list_case_dirs(disputes_dir: str, selector: str = "all") -> List[str]:
    """selector: 'all', or comma-separated case numbers / folder-name prefixes ('1,7,12-15')."""
    names = sorted(
        n for n in os.listdir(disputes_dir)
        if os.path.isfile(os.path.join(disputes_dir, n, "case.json"))
    )
    if selector == "all":
        chosen = names
    else:
        wanted = set()
        for part in selector.split(","):
            part = part.strip()
            m = re.fullmatch(r"(\d+)-(\d+)", part)
            if m:
                lo, hi = int(m.group(1)), int(m.group(2))
                wanted.update(n for n in names if lo <= _num(n) <= hi)
            elif part.isdigit():
                wanted.update(n for n in names if _num(n) == int(part))
            else:
                wanted.update(n for n in names if n.startswith(part))
        chosen = [n for n in names if n in wanted]
        if not chosen:
            raise SystemExit("no cases matched selector %r" % selector)
    return [os.path.join(disputes_dir, n) for n in chosen]


def _num(name: str) -> int:
    m = re.match(r"(\d+)", name)
    return int(m.group(1)) if m else -1

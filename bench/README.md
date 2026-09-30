# Strength-gap test set (Phase 1)

50 realistic, self-contained money disputes. We use them to measure the **strength gap**: how much better a strong model does than a weak one when two agents haggle openly, compared with the same cases run through Agent Arb.

All names, businesses and addresses are fictional. Nothing here calls a model.

## Do not show `answer.json` to the agents under test

Each case has a hidden answer key, `answer.json`. It must never be given to, or be readable by, an agent that is negotiating or being arbitrated. That includes the panel models. It is only for scoring afterwards.

Directory names are neutral, but a harness should still pass agents an opaque case id, not the folder name.

## Layout

```
bench/
  README.md
  validate.py
  disputes/
    NN-slug/
      case.json      what agents get (spec/case.schema.json, version 0.1)
      answer.json    HIDDEN answer key
      evidence/      plain-text contracts, messages, receipts, invoices
```

`case.json` follows `spec/case.schema.json`. `offers` is empty because offers come from the agents during a run. Every evidence entry has the file's real `sha256` and a relative `uri` such as `evidence/lease.txt`. `contract.evidenceId` points at the agreement the panel reads. Deposit cases use jurisdiction `US-CA`.

`answer.json`:

```json
{"owedCents": 0, "lowCents": 0, "highCents": 0, "reasoning": "...", "difficulty": "easy|medium|hard", "infoAsymmetry": false}
```

- `owedCents` is what the respondent should still pay the claimant, as of the day the case is filed. Where a landlord or seller already sent part of the money back, that part is not counted again.
- `lowCents` to `highCents` is the range a fair reader could reach from the files. Most cases are a single number.
- `reasoning` cites the evidence files that support the answer.
- `infoAsymmetry` is true when the respondent alone holds a decisive document (an internal log, a vendor invoice, a policy, a billing export) that the claimant would not have.

## The mix

| Type | Count |
|---|---|
| Security deposits (US-CA) | 25 |
| Refunds | 13 |
| Freelance invoices | 12 |

- 30 cases are person vs business, 20 are person vs person.
- Amounts run from $80 to $6,000.
- The claimant is the tenant (deposits), the customer or buyer (refunds) or the freelancer (invoices).
- Outcomes vary: 3 claimants are owed nothing, several are owed everything they contest, and most are owed something in between.
- Difficulty: 19 easy, 23 medium, 8 hard. 15 cases have information asymmetry.

## Who sees what (suggested for haggle runs)

Each evidence file records who submitted it (`partyId`). A simple rule for open haggling: both agents see the file named in `contract.evidenceId` and any `message` evidence; everything else is private to the party that submitted it. Agent Arb's fact panel sees all evidence. Whatever rule a run uses, use the same one for every case.

## California rules used

Only where the case depends on them and we are confident: itemized statement within 21 days, receipts for deductions over $125, normal wear and tear not deductible, no "non-refundable" deposits, no general turnover fees taken from a deposit, and the landlord's duty to try to re-let. Everything else is decided by the contract terms in the evidence. Statutory bad-faith penalties are not modeled.

## Checking the set

```
python3 bench/validate.py
```

It checks that every `case.json` validates against the schema (uses `jsonschema` if installed, otherwise a built-in checker), every `sha256` matches its file, ids and cross-references are consistent, and every `answer.json` has `low <= owed <= high`. It prints one line per case and a total, and exits non-zero on any failure. Re-run it after editing any evidence file, because editing a file changes its hash.

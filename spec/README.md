# Case format (draft v0.1)

Two files define what goes in and what comes out:

- [case.schema.json](case.schema.json): what agents submit. That's the parties, the contract, factual claims tied to evidence, required disclosures and one sealed final offer per side.
- [ruling.schema.json](ruling.schema.json): what comes back. That's the panel's vote on every claim, what the claimant is owed, the decision and a hash-chained record.

## Rules the format enforces

- **No arguments, only claims.** A claim is one short, checkable fact with evidence attached. There is no field for persuasion.
- **Display-only text is never read by the decision.** Evidence notes exist for humans reading the record.
- **The panel never sees which models the agents used.** That's recorded for the strength-gap benchmark only.
- **Offers stay sealed until the facts are found,** so neither side can anchor the panel.
- **Money is whole cents** to avoid rounding games.

## v1 decision rule (final-offer)

1. The panel finds what the claimant is owed (`owed.point`).
2. Reveal both sealed offers.
3. The offer closer to `owed.point` wins in full. On a tie, the person's offer wins, and between two people the claimant's offer wins.
4. If the panel can't reach a majority on a claim that changes the outcome, the case goes to `human_review`.

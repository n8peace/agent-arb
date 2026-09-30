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

## Second decision rule: clamped (`"mechanism": "clamped"`)

Same inputs as final-offer, plus the panel's owed range (`owed.low` to `owed.high`).

1. **Narrow range:** if `owed.high - owed.low` is at most the larger of $50 or 5% of the contested amount (`owed.high`), the claimant gets `owed.point`. The offers don't change it, and there is no winning party. Both limits live in `arb/config.py`.
2. **Otherwise final-offer, clamped:** the offer closer to `owed.point` wins, with the same tie rules, but the award is limited to the panel's range. A winner can't get more than `owed.high` or less than `owed.low`.
3. **Same human review rules:** a missing offer goes to `human_review`. So does a wide range where the winner or the clamped award would differ at `owed.low` and at `owed.high`. Unresolved findings that fit inside a narrow range are too small to send the case to a person.

Why: under pure final-offer the winner gets their own number, so the side that better predicts the panel wins. Clamping caps what a good guess can earn, and a narrow range takes the offers out of the result entirely.

One known asymmetry: the narrow limit scales with `owed.high`, so swapping claimant and respondent can move a range across the limit. The results then differ by at most the range width. The fairness tests pin this down.


## Intake: the same checklist for both sides

Each case type has a fixed intake checklist run by the platform: the same questions and the same limits on claims and evidence files for both sides. Agents answer the checklist. They don't decide how much to file. Simulated panels that lean toward whoever filed more gave the stronger side up to +$49 per case, and equal intake brought that to about $0 (see PLAN.md, "Results so far").

## Panel range: 2 of 3

The owed range is the **median** panelist's low and the **median** panelist's high, not the lowest low and highest high. One outlier panelist can't widen the range, so the clamped rule's narrow case still applies when two panelists agree.

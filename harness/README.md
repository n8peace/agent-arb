# Strength-gap harness (Phase 1 baseline)

Runs two AI agents haggling openly over each of the 50 cases in `bench/disputes/`, then scores each outcome against the hidden `answer.json`. This is the baseline Agent Arb has to beat.

Python 3, standard library only. **The default run is fully offline and makes no API calls.**

## Run it offline (free)

From the repo root:

```
python3 -m harness.run --provider scripted --cases all --out results/run1.json
python3 -m unittest
```

- `--cases` takes `all`, or a list such as `1,7,12-15`.
- `--out` defaults to `results/<timestamp>.json`. `results/` is git-ignored.
- `--rounds N` (default 6) and `--friction-cents N` (default 15000) override the values in `harness/config.py`.
- `--mechanism haggle` (default) is the open haggle. `--mechanism arb-final` and `--mechanism arb-clamped` run the same cases through the Agent Arb decision rules instead (`arb` is an alias for `arb-final`). Each side submits one sealed offer from only its own view of the case, and never sees the other side's offer. The panel is a stand-in that reads `answer.json` (owed amount and range), so these runs are an **UPPER BOUND, not a real result**. Scripted only.
- `--panel perfect|noisy|biased` (arb modes only, default `perfect`) swaps the stand-in panel. `noisy` = three simulated panelists at truth plus seeded noise (`--noise-pct`, sd as % of the contested amount, default 10); the panel point is their median and the range is min to max, widened by `--margin-pct` (default 0). `biased` = noisy plus a pull toward the side that filed more claims and evidence (`--bias`, default 0.5); a scripted weak side files only its stronger half of claims. `--seed` makes it repeatable. Still built from `answer.json`, so **not a real result**; the panel runs after both sealed offers and never reaches a prompt. Details and assumptions: `arb/simpanel.py`.
- `python3 -m arb.sweep --seeds 5 [--variants]` prints the seed-averaged grid (noise 0/5/10/20%, bias 0/0.5/1, arb-clamped and arb-final, haggle as reference): strength gaps, mean |award - owed| on awarded runs, and human-review rate. `--variants` adds candidate protections (2-of-3 median range, filing cap, equal filing).
- The scripted bargainers are our own rule-based stand-ins (weak concedes fast and anchors near the other side; strong anchors hard, concedes slowly and cites evidence ids). Their numbers only prove the pipeline works. They say nothing about real models.

## How a run works

1. `case_loader` reads `case.json` and the evidence files. It never opens `answer.json`.
2. `prompts` builds each side's view. Both sides see the contract and any `message` evidence. Every other document, and each side's own claims, are private to the side that submitted them. The other agent's model and `answer.json` are never shown. Agents get the opaque `caseId`, not the folder name.
3. `haggle` alternates turns (claimant first) for up to N rounds. Every reply must end with `OFFER: <whole cents>` or `ACCEPT`. One retry is allowed for a malformed last line. `ACCEPT` takes the other side's latest offer.
4. No deal (round cap or bad reply) means court: the claimant nets `owedCents - friction`, the respondent pays `owedCents + friction`.
5. `scoring` records deal amount, error versus `owedCents` (positive = claimant got more than owed) and which side gained. It runs all 4 pairings (weak/strong x weak/strong) per case.

**Strength gap** = average swing in a side's favor when its agent goes weak to strong, opponent held fixed, averaged over cases and both opponent strengths. Gains are measured against `owedCents`, including the friction cost of no deal. Full definitions are at the top of `harness/scoring.py`.

## Turning on real models later (costs money)

Nothing below is needed now. Real runs make paid API calls: 50 cases x 4 pairings x up to 12 turns is up to about 2,400 model calls per full run, plus retries. Get explicit approval for the spend first.

| Provider | Env var (API key) | Model id overrides (optional) |
|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY` | `ARB_ANTHROPIC_STRONG_MODEL`, `ARB_ANTHROPIC_WEAK_MODEL` |
| OpenAI | `OPENAI_API_KEY` | `ARB_OPENAI_STRONG_MODEL`, `ARB_OPENAI_WEAK_MODEL` |

Default model ids live in `harness/config.py`: Anthropic `claude-opus-5-5` (strong) and `claude-haiku-4-5-20251001` (weak). The OpenAI ids there (`gpt-5`, `gpt-5-mini`) are placeholders: check them before use.

A paid run also needs an explicit flag, so a key sitting in your environment never spends money by accident:

```
export ANTHROPIC_API_KEY=...   # your key
python3 -m harness.run --provider anthropic --cases 1-3 --i-understand-this-costs-money
```

Start with 1 to 3 cases. The Anthropic and OpenAI providers call the APIs over plain HTTPS (no SDK) and have not been run against the live APIs yet. `requirements.txt` lists optional SDKs only for future use.

## Files

`config.py` (all settings and model ids), `providers.py`, `case_loader.py`, `prompts.py`, `protocol.py` (OFFER/ACCEPT parsing), `haggle.py`, `scoring.py`, `run.py` (CLI), `tests/`.

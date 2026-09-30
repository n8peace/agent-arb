# Strength-gap harness (Phase 1 baseline)

Runs two AI agents haggling openly over each of the 50 cases in `bench/disputes/`, then scores each outcome against the hidden `answer.json`. This is the baseline Agent Arb has to beat.

Python 3, standard library only for offline runs and tests. **The default run is fully offline and makes no API calls.** Only the paid Anthropic provider needs the official `anthropic` SDK (see "Running with real models").

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

## Running with real models (costs money)

Nothing here is needed for offline work. Real runs make paid API calls: up to 50 cases x 4 pairings x 12 turns = 2,400 model calls per full run, plus retries. Get explicit approval for the spend first. **No paid call is ever made without all three of: an API key in the environment, `--i-understand-this-costs-money`, and `--max-usd N`.**

| Provider | Status | Env var (API key) | Model id overrides (optional) |
|---|---|---|---|
| Anthropic | official `anthropic` SDK, **not yet run against the live API** | `ANTHROPIC_API_KEY` | `ARB_ANTHROPIC_STRONG_MODEL`, `ARB_ANTHROPIC_WEAK_MODEL` |
| OpenAI | **UNTESTED**, plain HTTPS, no cost tracking, so `harness.run` refuses it | `OPENAI_API_KEY` | `ARB_OPENAI_STRONG_MODEL`, `ARB_OPENAI_WEAK_MODEL` |

Anthropic models, request settings and prices all live in `harness/config.py`:

| Tier | Model id | max_tokens | Request | Price per 1M tokens (in / out) |
|---|---|---|---|---|
| strong | `claude-opus-5-5` | 4000 | `output_config={"effort": "low"}`; no `thinking`, `budget_tokens`, temperature, top_p or prefill (thinking cannot be disabled on Opus 5.5 and counts against max_tokens) | $4.00 / $20.00 |
| weak | `claude-haiku-4-5` | 700 | plain request (no thinking, no effort) | $1.00 / $5.00 |

A reply whose `stop_reason` is `refusal` or `max_tokens` is treated as malformed and gets the usual one retry. Only `text` blocks are read. A 429 sleeps and retries up to 3 times; other API and connection errors stop the run (partial results are still saved). A model id that is not in `config.MODEL_PARAMS` and `config.PRICES_USD_PER_MTOK` is refused.

### One-time setup

```
python3 -m venv .venv
.venv/bin/pip install -r harness/requirements.txt
export ANTHROPIC_API_KEY=...   # your key, only in your shell, never in a file
```

`.venv/` is git-ignored. Offline runs and `python3 -m unittest` do not need it: the SDK is imported only when the Anthropic provider is built.

### 1. See the projected cost first (free, no network, no key)

```
python3 -m harness.run --estimate --cases 1-3
python3 -m harness.run --estimate --cases all
```

The estimate is cases x 4 pairings x rounds x 2 sides. Prompt size comes from the real built prompts at 4 chars per token. Output is priced at each model's `max_tokens` cap (worst case) and at 40% of it (typical). It assumes no early accepts and no retries. At the time of writing: 3 cases about $11.85 worst / $4.92 typical; all 50 cases about $197.38 worst / $81.82 typical. Rerun `--estimate` after changing anything in `config.py`.

### 2. Pilot: 3 cases, hard limit $10

```
.venv/bin/python -m harness.run --provider anthropic --cases 1-3 --max-usd 10 --i-understand-this-costs-money --out results/pilot.json
```

Check `results/pilot.json` (`summary.spend`, the transcripts, `malformedReplies`) before going further.

### 3. Full run with a budget

```
.venv/bin/python -m harness.run --provider anthropic --cases all --max-usd 100 --i-understand-this-costs-money --out results/full.json
```

`--max-usd 100` sits above the typical estimate. Use `--max-usd 200` to cover the worst case.

### How the budget works

- Before every call, the run adds the actual spend so far to that call's worst case (estimated prompt plus the full `max_tokens` reply). If that would pass `--max-usd`, it stops before sending the call.
- Spend is priced from the token counts the API returns (`response.usage`), including malformed and retried replies.
- On a stop, the pairings finished so far are scored and saved. The pairing in progress is dropped (its spend still counts). The output has `meta.completedRuns` vs `meta.plannedRuns`, and `summary.spend` (total USD, tokens, per-model split, `stoppedEarly`). Each run also has a `usage` block. The summary prints the spend and a STOPPED EARLY line. Strength gaps from a partial run cover only the cases that finished all 4 pairings.
- A budget stop exits 0. An API error stop saves partial results and exits 2.

## Files

`config.py` (all settings, model ids, request settings, prices), `providers.py`, `cost.py` (token accounting and the budget), `estimate.py` (offline cost estimate), `case_loader.py`, `prompts.py`, `protocol.py` (OFFER/ACCEPT parsing), `haggle.py`, `scoring.py`, `run.py` (CLI), `tests/`.

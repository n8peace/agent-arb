"""All tunable settings in one place. Change model ids here (or via env vars)."""
import os

# ---- model ids ---------------------------------------------------------
# "strong" and "weak" are the two capability tiers the benchmark compares.
# Each id can be overridden with an env var without editing this file.
MODELS = {
    "anthropic": {
        "strong": os.environ.get("ARB_ANTHROPIC_STRONG_MODEL", "claude-opus-5-5"),
        "weak": os.environ.get("ARB_ANTHROPIC_WEAK_MODEL", "claude-haiku-4-5"),
    },
    # Placeholders: confirm these ids against OpenAI's current model list before a real run.
    "openai": {
        "strong": os.environ.get("ARB_OPENAI_STRONG_MODEL", "gpt-5"),
        "weak": os.environ.get("ARB_OPENAI_WEAK_MODEL", "gpt-5-mini"),
    },
}

# ---- API keys (names of env vars only; keys are never stored in files) ----
API_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}

# Real (paid) providers also need this flag on the command line, so nobody
# spends money by accident just because a key happens to be in the environment.
PAID_CONFIRM_FLAG = "--i-understand-this-costs-money"

# ---- haggle rules --------------------------------------------------------
MAX_ROUNDS = 6            # one round = one turn per side (claimant speaks first)

# ---- per-model request settings (Anthropic) ------------------------------
# Keyed by model id. A model id not listed here is refused for paid runs, so no request is
# ever sent with guessed parameters or an unknown price.
#   max_tokens: cap on each reply. Thinking tokens count against it, so Opus needs more.
#   effort:     sent as output_config={"effort": ...}. None = do not send it at all.
# claude-opus-5-5: thinking cannot be disabled and is never sent (no "thinking", no
#   budget_tokens); no temperature/top_p; no assistant prefill.
# claude-haiku-4-5: no thinking param and no effort param (effort errors on Haiku 4.5).
MODEL_PARAMS = {
    "claude-opus-5-5": {"max_tokens": 4000, "effort": "low"},
    "claude-haiku-4-5": {"max_tokens": 700, "effort": None},
}

# ---- prices: USD per million tokens (input, output) ----------------------
PRICES_USD_PER_MTOK = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}

# ---- cost estimate assumptions --------------------------------------------
CHARS_PER_TOKEN = 4          # rough size of a prompt, for --estimate and the pre-call budget check
TYPICAL_OUTPUT_SHARE = 0.40  # a typical reply uses this share of its max_tokens cap
RATE_LIMIT_RETRIES = 3       # sleeps and retries after a 429, then gives up

# ---- scoring -------------------------------------------------------------
# No deal falls back to court: the claimant receives owedCents minus this
# friction, and the respondent pays owedCents plus this friction (whole cents).
COURT_FRICTION_CENTS = 15000   # $150 per side

# Strength levels used by the benchmark.
STRENGTHS = ("weak", "strong")

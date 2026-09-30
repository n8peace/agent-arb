"""All tunable settings in one place. Change model ids here (or via env vars)."""
import os

# ---- model ids ---------------------------------------------------------
# "strong" and "weak" are the two capability tiers the benchmark compares.
# Each id can be overridden with an env var without editing this file.
MODELS = {
    "anthropic": {
        "strong": os.environ.get("ARB_ANTHROPIC_STRONG_MODEL", "claude-opus-5-5"),
        "weak": os.environ.get("ARB_ANTHROPIC_WEAK_MODEL", "claude-haiku-4-5-20251001"),
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
MAX_TOKENS = 700          # cap on each real-model reply

# ---- scoring -------------------------------------------------------------
# No deal falls back to court: the claimant receives owedCents minus this
# friction, and the respondent pays owedCents plus this friction (whole cents).
COURT_FRICTION_CENTS = 15000   # $150 per side

# Strength levels used by the benchmark.
STRENGTHS = ("weak", "strong")

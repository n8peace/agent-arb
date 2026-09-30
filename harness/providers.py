"""Providers: one method, reply(system, messages) -> text.

ScriptedProvider runs fully offline. AnthropicProvider and OpenAIProvider call paid
APIs; they are never used by the tests or by default runs.
"""
import json
import os
import re
import urllib.error
import urllib.request
from typing import Dict, List, Optional

from . import config
from .prompts import SEALED_MODE_LINE
from .protocol import ACCEPT_LINE, format_offer, parse_action


class ProviderError(RuntimeError):
    pass


class Provider:
    name = "provider"

    def reply(self, system: str, messages: List[dict]) -> str:  # pragma: no cover - interface
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Scripted (offline, deterministic) bargainers
# ---------------------------------------------------------------------------

STRENGTH_PRESETS = {"weak": 0.2, "strong": 0.9}

# Sealed-offer strategy (arb mechanisms), as a share of the claim, per role and strength.
# The "claim" is the claimant's demand as each side can see it (see _sealed_offer).
#   weak   = naive: its own position, shaded 10% toward the other side.
#   strong = its estimate of what a neutral would find: claimants usually get most but not
#            all of a documented claim; a respondent that sees only its own side of the
#            dispute expects a neutral to land near the middle.
# Hand-picked, stylized stand-ins for reading the evidence, like the haggle constants.
SEALED_SHARE = {
    "claimant": {"weak": 0.90, "strong": 0.75},
    "respondent": {"weak": 0.10, "strong": 0.50},
}

_CLAIM_RE = re.compile(r"^CLAIM (\S+) \| amount_cents=(\d+|none) \| evidence: (\S+) \| (.*)$", re.M)
_DOC_RE = re.compile(r"^=== DOCUMENT (\S+) \| type: (\S+) \| submitted by: (\S+) \| (shared|private to you) ===$", re.M)
_ROLE_RE = re.compile(r"^YOU ARE: (claimant|respondent)", re.M)
_ROUNDS_RE = re.compile(r"At most (\d+) rounds")
_MONEY_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?")


def _money_cents(text: str) -> List[int]:
    vals = []
    for m in _MONEY_RE.finditer(text):
        dollars = int(m.group(1).replace(",", ""))
        vals.append(dollars * 100 + (int(m.group(2)) if m.group(2) else 0))
    return vals


class ScriptedProvider(Provider):
    """Rule-based bargainer. It reads only the prompt an agent would see.

    strength in [0, 1] ("weak" = 0.2, "strong" = 0.9):
      weak   opens near the other side, concedes a big share of the gap every turn,
             has a low walk-away point and gives up after a few turns.
      strong anchors hard, concedes a small share, holds a firm walk-away point,
             cites evidence by id, and only settles late.
    Everything is a pure function of (system, messages), so runs are repeatable.
    The numbers are stylized on purpose: this proves the pipeline, not real behavior.
    """

    def __init__(self, strength="weak"):
        self.strength_name = strength if isinstance(strength, str) else "custom"
        self.s = STRENGTH_PRESETS[strength] if isinstance(strength, str) else float(strength)
        self.name = "scripted-%s" % self.strength_name

    # -- parse the prompt ---------------------------------------------------
    @staticmethod
    def _view(system: str):
        role = _ROLE_RE.search(system).group(1)
        m_rounds = _ROUNDS_RE.search(system)
        rounds = int(m_rounds.group(1)) if m_rounds else 1   # sealed prompts have no rounds
        claims = []
        for m in _CLAIM_RE.finditer(system):
            claims.append((m.group(1), None if m.group(2) == "none" else int(m.group(2))))
        docs = [(m.group(1), m.group(2), m.group(4)) for m in _DOC_RE.finditer(system)]
        # text of shared docs (used for a fallback number)
        shared_text = []
        parts = re.split(r"^=== DOCUMENT ", system, flags=re.M)
        for p in parts[1:]:
            if "| shared ===" in p.split("\n", 1)[0]:
                shared_text.append(p)
        return role, rounds, claims, docs, "\n".join(shared_text)

    @staticmethod
    def _their_offers(messages: List[dict]) -> List[int]:
        out = []
        for m in messages:
            if m["role"] == "user":
                a = parse_action(m["content"])
                if a and a[0] == "offer":
                    out.append(a[1])
        return out

    @staticmethod
    def _my_offers(messages: List[dict]) -> List[int]:
        out = []
        for m in messages:
            if m["role"] == "assistant":
                a = parse_action(m["content"])
                if a and a[0] == "offer":
                    out.append(a[1])
        return out

    # -- decision -------------------------------------------------------------
    def reply(self, system: str, messages: List[dict]) -> str:
        role, rounds, claims, docs, shared_text = self._view(system)
        if SEALED_MODE_LINE in system:
            return self._sealed_offer(system, role, claims, docs, shared_text)
        s = self.s
        their = self._their_offers(messages)
        mine = self._my_offers(messages)
        turn = len(mine)  # 0-based index of this turn for this side
        cites = self._citations(docs)

        amounts = [a for _, a in claims if a is not None]
        if role == "claimant":
            # belief: the biggest amount I claim; else the biggest dollar figure in shared docs
            fallback = max(_money_cents(shared_text), default=0)
            belief = max(amounts) if amounts else fallback
            if turn == 0:
                offer = int(round(belief * (0.7 + 0.4 * s)))
                return self._compose(role, "open", offer, cites)
            # walk-away point: how far toward the other side's opening offer I will go
            floor = int(round(belief - self._give_up() * (belief - their[0])))
            return self._respond_claimant(their[-1], mine[-1], floor, turn, rounds, cites)

        # respondent: needs the claimant's demand before it can form a view
        demand = their[0] if their else 0
        below = [a for a in amounts if a < demand]
        belief = demand - max(below) if below else demand // 2
        belief = max(0, belief)
        cap = belief + int(round(self._give_up() * (demand - belief)))
        if turn == 0:
            offer = belief + int(round((demand - belief) * (0.3 - 0.3 * s)))
            return self._compose(role, "open", min(offer, cap), cites)
        return self._respond_respondent(their[-1], mine[-1], cap, turn, rounds, cites)

    def _sealed_offer(self, system, role, claims, docs, shared_text) -> str:
        """One sealed offer from this side's own view only (the system prompt). It never sees
        the other side's offer, so the number cannot depend on it."""
        amounts = [a for _, a in claims if a is not None]
        if role == "claimant":
            # the claim: the biggest amount I claim; else the biggest dollar figure in shared docs
            claim = max(amounts) if amounts else max(_money_cents(shared_text), default=0)
        else:
            # the claim as I can see it: the total of my own disputed amounts (usually what I
            # withheld or refused); else the biggest dollar figure in any document I can see
            visible = system.split("SHARED DOCUMENTS", 1)[-1].split("YOUR CLAIMS", 1)[0]
            claim = sum(amounts) if amounts else max(_money_cents(visible), default=0)
        tier = "strong" if self.s >= 0.5 else "weak"
        offer = int(round(claim * SEALED_SHARE[role][tier]))
        return self._compose(role, "sealed", offer, self._citations(docs))

    def _give_up(self) -> float:
        # share of the distance to the other side's opening offer that I will travel at most
        return 1.0 - 0.7 * self.s

    def _step(self) -> float:
        return 0.55 - 0.5 * self.s  # share of the gap conceded per turn

    def _patient_until(self, rounds: int) -> int:
        # first own-turn index at which a firm-enough offer will be accepted outright
        return 2 if self.s < 0.5 else rounds - 1

    def _respond_claimant(self, their_last, my_last, floor, turn, rounds, cites):
        nxt = int(round(my_last + self._step() * (their_last - my_last)))
        nxt = max(nxt, floor)
        if their_last >= nxt or (their_last >= floor and turn >= self._patient_until(rounds)):
            return self._compose("claimant", "accept", their_last, cites)
        return self._compose("claimant", "counter", nxt, cites)

    def _respond_respondent(self, their_last, my_last, cap, turn, rounds, cites):
        nxt = int(round(my_last + self._step() * (their_last - my_last)))
        nxt = min(nxt, cap)
        if their_last <= nxt or (their_last <= cap and turn >= self._patient_until(rounds)):
            return self._compose("respondent", "accept", their_last, cites)
        return self._compose("respondent", "counter", nxt, cites)

    # -- wording ------------------------------------------------------------
    def _citations(self, docs):
        # own private documents first, then shared ones
        private = [d[0] for d in docs if d[2] == "private to you"]
        shared = [d[0] for d in docs if d[2] == "shared"]
        return private + shared

    def _compose(self, role: str, kind: str, cents: int, cites: List[str]) -> str:
        strong = self.s >= 0.5
        cents = max(0, int(cents))
        if kind == "accept":
            body = ("Agreed. That amount is fine with me." if not strong
                    else "That is within what the record supports. We have a deal.")
            return "%s\n%s" % (body, ACCEPT_LINE)
        ev = ""
        if strong and cites:
            ev = " Relevant records: %s." % ", ".join(cites[:4])
        if kind == "sealed":
            body = ("This is my offer." if not strong
                    else "This is my estimate of what the record supports.%s" % ev)
        elif kind == "open":
            body = ("Hello. I would like to settle this quickly and fairly." if not strong
                    else "Based on the documents, this is the amount that is owed.%s I will not move far from it." % ev)
        else:
            body = ("OK, I can move toward you." if not strong
                    else "The evidence has not changed, so I am moving only slightly.%s" % ev)
        return "%s\n%s" % (body, format_offer(cents))


# ---------------------------------------------------------------------------
# Paid providers (stubs). Never used by tests or default runs.
# ---------------------------------------------------------------------------

def _require_key(provider: str) -> str:
    var = config.API_KEY_ENV[provider]
    key = os.environ.get(var, "").strip()
    if not key:
        raise ProviderError(
            "%s is not set. Set it to your %s API key to use the %s provider. "
            "Real runs cost money; use --provider scripted for a free offline run."
            % (var, provider, provider))
    return key


def _post_json(url: str, headers: Dict[str, str], body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ProviderError("API error %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:500]))
    except urllib.error.URLError as e:
        raise ProviderError("network error: %s" % e)


class AnthropicProvider(Provider):
    """Anthropic Messages API over plain HTTPS (no SDK needed). Untested against the live API."""

    def __init__(self, strength: str, model: Optional[str] = None):
        self._key = _require_key("anthropic")
        self.model = model or config.MODELS["anthropic"][strength]
        self.name = "anthropic:%s" % self.model

    def reply(self, system: str, messages: List[dict]) -> str:
        data = _post_json(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": self._key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            {"model": self.model, "max_tokens": config.MAX_TOKENS, "system": system, "messages": messages})
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


class OpenAIProvider(Provider):
    """OpenAI Chat Completions over plain HTTPS (no SDK needed). Untested against the live API."""

    def __init__(self, strength: str, model: Optional[str] = None):
        self._key = _require_key("openai")
        self.model = model or config.MODELS["openai"][strength]
        self.name = "openai:%s" % self.model

    def reply(self, system: str, messages: List[dict]) -> str:
        data = _post_json(
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": "Bearer %s" % self._key, "content-type": "application/json"},
            {"model": self.model, "messages": [{"role": "system", "content": system}] + list(messages)})
        return data["choices"][0]["message"]["content"] or ""


def make_provider(kind: str, strength: str) -> Provider:
    if kind == "scripted":
        return ScriptedProvider(strength)
    if kind == "anthropic":
        return AnthropicProvider(strength)
    if kind == "openai":
        return OpenAIProvider(strength)
    raise ValueError("unknown provider %r" % kind)

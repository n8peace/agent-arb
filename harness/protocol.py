"""The turn protocol: every reply must END with one machine-readable line."""
import re
from typing import Optional, Tuple

_LAST = re.compile(r"^\s*(?:OFFER:\s*(\d{1,3}(?:,\d{3})+|\d+)|(ACCEPT))\s*$")


def parse_action(text: str) -> Optional[Tuple[str, Optional[int]]]:
    """Return ("offer", cents), ("accept", None), or None if the last line is not valid.

    Only the last non-empty line counts. Offers are whole cents; "$" and decimals are rejected.
    """
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return None
    m = _LAST.match(lines[-1])
    if not m:
        return None
    if m.group(2):
        return ("accept", None)
    return ("offer", int(m.group(1).replace(",", "")))


def format_offer(cents: int) -> str:
    return "OFFER: %d" % int(cents)


ACCEPT_LINE = "ACCEPT"

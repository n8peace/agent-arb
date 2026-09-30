"""Canonical-JSON hashing and chaining for the ruling "record".

canonical_json(obj): keys sorted, no whitespace, ASCII-escaped, integers only
(floats are rejected so a hash can't depend on float formatting).

chain(body, previous): the record for a ruling body.
    digest = sha256( (previous or "") + "\\n" + canonical_json(body) )
The previous hash goes in first, then the new content, so each record commits
to the whole log before it. previous=None marks the first record.
"""
import hashlib
import json
from typing import Optional

HEX64 = 64


def _check(obj, path="$"):
    if isinstance(obj, float):
        raise TypeError("floats are not allowed in a hashed record (%s)" % path)
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise TypeError("object keys must be strings (%s)" % path)
            _check(v, "%s.%s" % (path, k))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _check(v, "%s[%d]" % (path, i))
    elif obj is not None and not isinstance(obj, (str, int, bool)):
        raise TypeError("unsupported type %s (%s)" % (type(obj).__name__, path))


def canonical_json(obj) -> str:
    _check(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chain(body: dict, previous: Optional[str] = None) -> dict:
    """Return {"sha256": <new hash>, "previous": <previous hash or None>}."""
    if previous is not None and not (isinstance(previous, str) and len(previous) == HEX64
                                     and all(c in "0123456789abcdef" for c in previous)):
        raise ValueError("previous must be a lowercase 64-character sha256 hex string or None")
    digest = sha256_hex((previous or "") + "\n" + canonical_json(body))
    return {"sha256": digest, "previous": previous}


def verify(body: dict, record: dict) -> bool:
    """True if record matches body and its own previous hash."""
    return chain(body, record["previous"])["sha256"] == record["sha256"]

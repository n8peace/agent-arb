#!/usr/bin/env python3
"""Validate the Agent Arb strength-gap test set.

Checks, per dispute directory under bench/disputes/:
  - case.json validates against spec/case.schema.json
      (uses the `jsonschema` package if installed, otherwise a small built-in
       validator that covers the keywords the schema uses)
  - every evidence sha256 matches the file its uri points to
  - ids are consistent (claims and contract point at real evidence and parties)
  - answer.json is well formed and lowCents <= owedCents <= highCents

Prints one line per case and a total. Exits 1 on any failure.
Standard library only.
"""
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DISPUTES = os.path.join(HERE, "disputes")
SCHEMA_PATH = os.path.join(HERE, "..", "spec", "case.schema.json")
DIFFICULTIES = {"easy", "medium", "hard"}
EXPECTED_COUNTS = None  # set to a dict like {"security_deposit": 25} to enforce a mix

try:
    import jsonschema  # type: ignore
except ImportError:
    jsonschema = None


# ---------------------------------------------------------------- schema check
def _type_ok(value, t):
    if isinstance(t, list):
        return any(_type_ok(value, x) for x in t)
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    if t == "string":
        return isinstance(value, str)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "null":
        return value is None
    return True


def _resolve(root, ref):
    assert ref.startswith("#/"), ref
    node = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def mini_validate(value, schema, root, path="$"):
    """Small JSON Schema subset validator. Returns a list of error strings."""
    errors = []
    if "$ref" in schema:
        errors += mini_validate(value, _resolve(root, schema["$ref"]), root, path)
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {schema['enum']}")
    if "type" in schema and not _type_ok(value, schema["type"]):
        errors.append(f"{path}: expected {schema['type']}, got {type(value).__name__}")
        return errors
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: {value!r} does not match {schema['pattern']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, item in enumerate(value):
                errors += mini_validate(item, schema["items"], root, f"{path}[{i}]")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required '{key}'")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    errors.append(f"{path}: unexpected property '{key}'")
        for key, sub in props.items():
            if key in value:
                errors += mini_validate(value[key], sub, root, f"{path}.{key}")
    return errors


def schema_errors(case, schema):
    if jsonschema is not None:
        validator = jsonschema.Draft202012Validator(schema)
        return [f"{'/'.join(str(p) for p in e.absolute_path) or '$'}: {e.message}"
                for e in validator.iter_errors(case)]
    return mini_validate(case, schema, schema)


# ---------------------------------------------------------------- per-case checks
def check_case(case_dir, schema):
    errors = []
    case_path = os.path.join(case_dir, "case.json")
    answer_path = os.path.join(case_dir, "answer.json")

    try:
        with open(case_path) as f:
            case = json.load(f)
    except (OSError, ValueError) as exc:
        return None, [f"case.json unreadable: {exc}"]

    errors += [f"schema {e}" for e in schema_errors(case, schema)]
    if errors:
        return case, errors  # structural problems make the rest unreliable

    party_ids = {p["partyId"] for p in case["parties"]}
    if {p["role"] for p in case["parties"]} != {"claimant", "respondent"}:
        errors.append("parties must be one claimant and one respondent")

    ev_ids = set()
    for ev in case["evidence"]:
        if ev["evidenceId"] in ev_ids:
            errors.append(f"duplicate evidenceId {ev['evidenceId']}")
        ev_ids.add(ev["evidenceId"])
        if ev["partyId"] not in party_ids:
            errors.append(f"evidence {ev['evidenceId']} has unknown partyId")
        uri = ev.get("uri")
        if not uri:
            errors.append(f"evidence {ev['evidenceId']} has no uri to hash-check")
            continue
        path = os.path.normpath(os.path.join(case_dir, uri))
        if not path.startswith(os.path.abspath(case_dir)) or not os.path.isfile(path):
            errors.append(f"evidence file missing: {uri}")
            continue
        with open(path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        if digest != ev["sha256"]:
            errors.append(f"sha256 mismatch: {uri}")

    if case["contract"]["evidenceId"] not in ev_ids:
        errors.append("contract.evidenceId not in evidence")
    for cl in case["claims"]:
        if cl["partyId"] not in party_ids:
            errors.append(f"claim {cl['claimId']} has unknown partyId")
        for eid in cl["evidenceIds"]:
            if eid not in ev_ids:
                errors.append(f"claim {cl['claimId']} cites unknown evidence {eid}")
    if len({cl["partyId"] for cl in case["claims"]}) < 2:
        errors.append("both parties must submit at least one claim")
    if case["offers"] != []:
        errors.append("offers must be empty in the test set")
    if case["kind"] == "security_deposit" and case["contract"].get("jurisdiction") != "US-CA":
        errors.append("security deposit cases must use jurisdiction US-CA")

    # answer key
    try:
        with open(answer_path) as f:
            ans = json.load(f)
    except (OSError, ValueError) as exc:
        errors.append(f"answer.json unreadable: {exc}")
        return case, errors
    expected = {"owedCents", "lowCents", "highCents", "reasoning", "difficulty", "infoAsymmetry"}
    if set(ans) != expected:
        errors.append(f"answer.json keys must be exactly {sorted(expected)}")
    else:
        for k in ("owedCents", "lowCents", "highCents"):
            if not (isinstance(ans[k], int) and not isinstance(ans[k], bool) and ans[k] >= 0):
                errors.append(f"answer.{k} must be a non-negative integer")
        if all(isinstance(ans[k], int) for k in ("owedCents", "lowCents", "highCents")):
            if not ans["lowCents"] <= ans["owedCents"] <= ans["highCents"]:
                errors.append("answer must satisfy low <= owed <= high")
        if not (isinstance(ans["reasoning"], str) and ans["reasoning"].strip()):
            errors.append("answer.reasoning must be a non-empty string")
        if ans["difficulty"] not in DIFFICULTIES:
            errors.append("answer.difficulty must be easy, medium or hard")
        if not isinstance(ans["infoAsymmetry"], bool):
            errors.append("answer.infoAsymmetry must be a boolean")
    return case, errors


def main():
    with open(SCHEMA_PATH) as f:
        schema = json.load(f)
    mode = "jsonschema" if jsonschema is not None else "built-in validator (jsonschema not installed)"
    print(f"Schema check: {mode}")

    if not os.path.isdir(DISPUTES):
        print(f"FAIL no disputes directory at {DISPUTES}")
        return 1
    names = sorted(d for d in os.listdir(DISPUTES) if os.path.isdir(os.path.join(DISPUTES, d)))
    passed = 0
    kinds = {}
    seen_ids = set()
    for name in names:
        case, errors = check_case(os.path.join(DISPUTES, name), schema)
        if case and case.get("caseId") in seen_ids:
            errors.append("duplicate caseId")
        if case:
            seen_ids.add(case.get("caseId"))
        if not re.match(r"^\d{2}-[a-z0-9-]+$", name):
            errors.append("directory name must look like NN-slug")
        if errors:
            print(f"FAIL {name}")
            for e in errors:
                print(f"     - {e}")
        else:
            passed += 1
            kinds[case["kind"]] = kinds.get(case["kind"], 0) + 1
            print(f"PASS {name}")

    total = len(names)
    print(f"\n{passed}/{total} cases passed")
    if kinds:
        print("By kind: " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    if EXPECTED_COUNTS is not None and kinds != EXPECTED_COUNTS:
        print(f"FAIL expected mix {EXPECTED_COUNTS}")
        return 1
    return 0 if passed == total and total > 0 else 1


if __name__ == "__main__":
    sys.exit(main())

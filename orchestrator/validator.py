"""Validator + self-consistency voting + recovery.

Two jobs:
  1. validate_tool_calls: check parsed tool_calls against the provided tool
     schemas (name exists, required args present). Uses jsonschema when a
     parameter schema is available.
  2. vote: given N raw DeepSeek answers (from bridge.fan_out), parse each,
     and pick the most-agreed-upon result. This is the lever that turns a
     flaky single call into a sturdier decision: if 2 of 3 windows say
     "call terminal with `df -h`", we trust that over a lone outlier.
"""
import json
import logging
from collections import Counter
from typing import List, Dict, Any, Optional, Tuple

try:
    from jsonschema import validate as js_validate, ValidationError
except Exception:  # pragma: no cover
    js_validate = None
    ValidationError = Exception

import toolcall as toolcall

logger = logging.getLogger("orchestrator.validator")


def _schema_for(tools: Optional[List[Dict[str, Any]]], name: str) -> Optional[dict]:
    if not tools:
        return None
    for t in tools:
        fn = t.get("function", t)
        if fn.get("name") == name:
            return fn.get("parameters")
    return None


def _tool_names(tools: Optional[List[Dict[str, Any]]]) -> set:
    if not tools:
        return set()
    return {(_t.get("function", _t)).get("name") for _t in tools}


def validate_tool_calls(calls: List[Dict[str, Any]],
                        tools: Optional[List[Dict[str, Any]]]) -> Tuple[bool, str]:
    """Return (ok, error_message)."""
    names = _tool_names(tools)
    for call in calls:
        fn = call.get("function", {})
        name = fn.get("name")
        if names and name not in names:
            return False, f"unknown tool '{name}'"
        try:
            args = json.loads(fn.get("arguments", "{}"))
        except Exception as e:
            return False, f"arguments not valid json for '{name}': {e}"
        schema = _schema_for(tools, name)
        if schema and js_validate is not None:
            try:
                js_validate(instance=args, schema=schema)
            except ValidationError as e:
                return False, f"args for '{name}' violate schema: {e.message}"
    return True, ""


def _fingerprint(calls: Optional[List[Dict[str, Any]]], content: Optional[str]) -> str:
    """Canonical signature of a parsed answer for voting."""
    if calls:
        sig = []
        for c in calls:
            fn = c.get("function", {})
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except Exception:
                args = fn.get("arguments")
            sig.append((fn.get("name"), json.dumps(args, sort_keys=True, ensure_ascii=False)))
        return "TOOLS::" + json.dumps(sorted(sig), ensure_ascii=False)
    # for plain text we can't meaningfully vote on exact wording; bucket as TEXT
    return "TEXT"


def vote(raw_answers: List[str],
         tools: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Parse all answers, validate, and pick the consensus.

    Returns dict:
      {kind: "tool_calls"|"text", tool_calls?, content?, votes, total,
       parsed_stats}
    """
    parsed = []  # (calls, content, how, valid, fp)
    stats = Counter()
    for raw in raw_answers:
        if not raw:
            stats["empty"] += 1
            continue
        calls, content, how = toolcall.parse(raw)
        stats[how] += 1
        valid = True
        if calls:
            ok, _ = validate_tool_calls(calls, tools)
            valid = ok
            if not ok:
                stats["invalid"] += 1
        parsed.append((calls, content, how, valid, _fingerprint(calls if valid else None, content)))

    # Only vote among valid parses (text always counts as valid)
    valid_parses = [p for p in parsed if p[3]]
    if not valid_parses:
        # fall back to any text we got
        for calls, content, how, valid, fp in parsed:
            if content:
                return {"kind": "text", "content": content, "votes": 1,
                        "total": len(raw_answers), "parsed_stats": dict(stats)}
        return {"kind": "text", "content": "", "votes": 0,
                "total": len(raw_answers), "parsed_stats": dict(stats)}

    fp_counter = Counter(p[4] for p in valid_parses)
    winner_fp, votes = fp_counter.most_common(1)[0]
    winner = next(p for p in valid_parses if p[4] == winner_fp)
    calls, content, how, _, _ = winner

    if calls:
        return {"kind": "tool_calls", "tool_calls": calls, "content": content,
                "votes": votes, "total": len(raw_answers), "parsed_stats": dict(stats)}
    # text consensus: return the longest text among the winners (most complete)
    texts = [p[1] for p in valid_parses if not p[0] and p[1]]
    best = max(texts, key=len) if texts else (content or "")
    return {"kind": "text", "content": best, "votes": votes,
            "total": len(raw_answers), "parsed_stats": dict(stats)}


def recovery_prompt(original_prompt: str, error: str) -> str:
    """Short recovery prompt appended when validation failed."""
    return (
        original_prompt
        + "\n\n========================================\n"
        + f"YOUR PREVIOUS RESPONSE WAS INVALID: {error}\n"
        + "Return a corrected response. If using a tool, output ONLY the JSON "
        + "object {\"tool_calls\":[{\"name\":...,\"arguments\":{...}}]} with no "
        + "other text. Ensure all required arguments are present and types match.\n"
        + "========================================\n"
    )

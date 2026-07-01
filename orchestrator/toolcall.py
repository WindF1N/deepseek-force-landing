"""Normalize DeepSeek's raw text answer into native OpenAI tool_calls.

This is the load-bearing piece of the whole bridge. Hermes' agent loop expects
the assistant message to carry a structured `tool_calls` array
(each: id + function.name + function.arguments-as-json-string). DeepSeek-web
returns plain text. Our pre-instruction nudges it to emit
    {"tool_calls":[{"name":"...","arguments":{...}}]}
but it sometimes wraps that in markdown, adds prose, or returns plain prose
when it decides no tool is needed. We handle all of that here.

Strategy (in order):
  1. Try strict json.loads on the trimmed string.
  2. Strip ```json fences and retry.
  3. Pull the first balanced {...} block and retry.
  4. json_repair as last resort.
If a dict with "tool_calls" is found -> convert to OpenAI structure.
Otherwise -> treat the text as a normal assistant content reply.
"""
import json
import re
import uuid
from typing import Optional, Tuple, List, Dict, Any

try:
    from json_repair import repair_json
except Exception:  # pragma: no cover
    repair_json = None


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _balanced_object(s: str) -> Optional[str]:
    """Return the first balanced {...} substring, respecting strings/escapes."""
    start = s.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def _try_load(text: str) -> Optional[Any]:
    candidates: List[str] = []
    t = text.strip()
    candidates.append(t)
    m = _FENCE_RE.search(t)
    if m:
        candidates.append(m.group(1).strip())
    bo = _balanced_object(t)
    if bo:
        candidates.append(bo)
    for c in candidates:
        try:
            return json.loads(c)
        except Exception:
            continue
    # last resort: json_repair on the most promising candidate
    if repair_json is not None:
        for c in candidates:
            try:
                fixed = repair_json(c)
                obj = json.loads(fixed)
                if obj not in ({}, [], "", None):
                    return obj
            except Exception:
                continue
    return None


def salvage_truncated(text: str) -> Optional[Dict[str, Any]]:
    """Recover a tool_call from a stream that DeepSeek cut off mid-argument.

    The classic failure: a huge write_file whose `content` string is enormous;
    the model emits {"tool_calls":[{"name":"write_file","arguments":{"path":
    "x","content":"<thousands of chars>  and just STOPS. Strict json.loads and
    json_repair both bail. We dig out the tool name and any complete leading
    args, plus the last partial string value, so the agent gets a usable call
    instead of garbage. Best-effort: returns a {name,arguments} dict or None.
    """
    if not text:
        return None
    name_m = re.search(r'"(?:name|tool|tool_name)"\s*:\s*"([^"]+)"', text)
    if not name_m:
        return None
    name = name_m.group(1)
    args: Dict[str, Any] = {}
    a_start = text.find("arguments")
    if a_start != -1:
        brace = text.find("{", a_start)
        if brace != -1:
            frag = text[brace:]
            bo = _balanced_object(frag)
            if bo:
                try:
                    args = json.loads(bo)
                except Exception:
                    if repair_json is not None:
                        try:
                            args = json.loads(repair_json(bo))
                        except Exception:
                            args = {}
            if not args:
                # truncated: pull complete "key":"value" pairs, then dangling key
                for k, v in re.findall(r'"([^"]+)"\s*:\s*"((?:[^"\\]|\\.)*)"', frag):
                    try:
                        args[k] = json.loads('"' + v + '"')
                    except Exception:
                        args[k] = v
                tail = re.search(r'"([^"]+)"\s*:\s*"((?:[^"\\]|\\.)*)$', frag)
                if tail and tail.group(1) not in args:
                    try:
                        args[tail.group(1)] = json.loads('"' + tail.group(2) + '"')
                    except Exception:
                        args[tail.group(1)] = tail.group(2)
    return {"name": name, "arguments": args} if args else None


def _normalize_calls(raw_calls: list) -> List[Dict[str, Any]]:
    """Convert [{name,arguments}] (our pre-instruction format) or already-OpenAI
    shaped entries into strict OpenAI tool_calls."""
    out = []
    for call in raw_calls:
        if not isinstance(call, dict):
            continue
        # already-openai shape
        if "function" in call and isinstance(call["function"], dict):
            fn = call["function"]
            name = fn.get("name")
            args = fn.get("arguments")
        else:
            name = call.get("name") or call.get("tool") or call.get("tool_name")
            args = call.get("arguments", call.get("args", {}))
        if not name:
            continue
        if isinstance(args, (dict, list)):
            args_str = json.dumps(args, ensure_ascii=False)
        elif isinstance(args, str):
            # ensure it's valid json string; if not, wrap
            try:
                json.loads(args)
                args_str = args
            except Exception:
                args_str = json.dumps({"_raw": args}, ensure_ascii=False)
        else:
            args_str = "{}"
        out.append({
            "id": "call_" + uuid.uuid4().hex[:24],
            "type": "function",
            "function": {"name": name, "arguments": args_str},
        })
    return out


def parse(text: str) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str], str]:
    """Parse a DeepSeek answer.

    Returns (tool_calls, content, how):
      - tool_calls: list of native OpenAI tool_calls, or None
      - content: assistant text (when no tool call), or None
      - how: "clean" | "extracted" | "repaired" | "text" | "empty"
    """
    if not text or not text.strip():
        return None, "", "empty"

    stripped = text.strip()
    obj = _try_load(text)
    
    # Extract reasoning: any prose BEFORE the JSON block
    reasoning = _extract_reasoning(stripped)

    if isinstance(obj, dict) and "tool_calls" in obj and isinstance(obj["tool_calls"], list):
        calls = _normalize_calls(obj["tool_calls"])
        if calls:
            # was it the whole string (clean) or did we have to dig it out?
            how = "clean" if stripped.startswith("{") and stripped.endswith("}") else "extracted"
            return calls, reasoning, how

    # single bare call object: {"name":...,"arguments":...}
    if isinstance(obj, dict) and ("name" in obj or "tool" in obj) and "arguments" in obj:
        calls = _normalize_calls([obj])
        if calls:
            return calls, reasoning, "extracted"

    # truncated stream: looks like a tool_call but JSON never closed (huge args)
    if '"tool_calls"' in stripped or '"arguments"' in stripped:
        salvaged = salvage_truncated(stripped)
        if salvaged:
            calls = _normalize_calls([salvaged])
            if calls:
                return calls, reasoning, "salvaged"

    # function-call style: DeepSeek writes write_file({...}) instead of JSON
    fc = _parse_funccall_style(stripped)
    if fc:
        calls = _normalize_calls(fc)
        if calls:
            return calls, reasoning, "funccall"

    # no tool call -> it's a normal text reply. Strip leaked transcript markers.
    return None, _clean_text(text), "text"


def _extract_reasoning(text: str) -> Optional[str]:
    """Extract reasoning/explanation text that appears before JSON tool_calls.
    
    Example:
      'I need to check the file first.\n\n{"tool_calls": [...]}'
      → Returns: 'I need to check the file first.'
    """
    # Find the first { that starts a JSON object
    json_start = text.find('{')
    if json_start == -1:
        return None
    
    # Everything before JSON is potential reasoning
    before = text[:json_start].strip()
    
    # Filter out markdown fences and other noise
    before = re.sub(r'```(?:json)?', '', before).strip()
    
    # If it's substantial (more than just whitespace/punctuation), keep it
    if len(before) > 5 and not before.isspace():
        return before
    
    return None


_FUNCCALL_RE = re.compile(r'\b([a-zA-Z_][a-zA-Z0-9_]{2,40})\s*\(\s*(\{)', re.S)


def _parse_funccall_style(s: str) -> List[Dict[str, Any]]:
    """Catch `name({json args})` calls DeepSeek emits as prose instead of our
    JSON protocol. Returns list of {name, arguments} dicts."""
    out: List[Dict[str, Any]] = []
    for m in _FUNCCALL_RE.finditer(s):
        name = m.group(1)
        if name in ("function", "object", "json", "tool_calls"):
            continue
        bo = _balanced_object(s[m.start(2):])
        if not bo:
            continue
        args = None
        try:
            args = json.loads(bo)
        except Exception:
            if repair_json is not None:
                try:
                    args = json.loads(repair_json(bo))
                except Exception:
                    args = None
        if isinstance(args, dict):
            out.append({"name": name, "arguments": args})
    return out


_MARKER_RE = re.compile(
    r"^\s*#?\s*(SYSTEM INSTRUCTIONS|USER|ASSISTANT( \(called tools\))?|TOOL RESULT( \(for [^)]*\))?)\s*$",
    re.MULTILINE,
)


def _clean_text(text: str) -> str:
    """Remove transcript section markers DeepSeek sometimes echoes back."""
    t = text.strip()
    # drop standalone marker lines
    t = _MARKER_RE.sub("", t)
    # drop inline 'TOOL RESULT (for call_...) {json}' echoes
    t = re.sub(r"TOOL RESULT \(for [^)]*\):?", "", t)
    # collapse blank runs
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t


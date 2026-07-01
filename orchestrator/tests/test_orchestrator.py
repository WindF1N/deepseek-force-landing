"""Regression tests for the DeepSeek Orchestrator (pure logic, no live Bridge).

Run:
    cd ~/deepseek-api/orchestrator
    ../venv/bin/python -m pytest tests/test_orchestrator.py -q
or without pytest:
    ../venv/bin/python tests/test_orchestrator.py

These cover the parsing/validation/voting/routing logic that two real bugs
hid in (web-search breaking tool protocol, and 'deep' matching 'deepseek').
Network-dependent end-to-end behavior is verified manually (see README).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import orchestrator.toolcall as toolcall
import orchestrator.validator as validator
import orchestrator.prompt as pb

TOOLS = [{
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a text file.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
}]


# ---------- toolcall.parse ----------
def test_clean_json_tool_call():
    c, t, how = toolcall.parse('{"tool_calls":[{"name":"read_file","arguments":{"path":"/x"}}]}')
    assert c and c[0]["function"]["name"] == "read_file"
    assert how == "clean"
    assert json.loads(c[0]["function"]["arguments"]) == {"path": "/x"}
    assert c[0]["id"].startswith("call_")
    assert c[0]["type"] == "function"


def test_markdown_fenced_json():
    c, t, how = toolcall.parse('```json\n{"tool_calls":[{"name":"read_file","arguments":{"path":"/y"}}]}\n```')
    assert c and c[0]["function"]["name"] == "read_file"
    assert how == "extracted"


def test_prose_wrapped_json():
    c, t, how = toolcall.parse('Sure: {"tool_calls":[{"name":"read_file","arguments":{"path":"/z"}}]} done')
    assert c and c[0]["function"]["name"] == "read_file"


def test_plain_text():
    c, t, how = toolcall.parse("The hostname is finland.")
    assert c is None and t == "The hostname is finland." and how == "text"


def test_empty():
    c, t, how = toolcall.parse("")
    assert c is None and how == "empty"


def test_bare_call_object():
    c, t, how = toolcall.parse('{"name":"read_file","arguments":{"path":"/a"}}')
    assert c and c[0]["function"]["name"] == "read_file"


# ---------- salvage truncated huge tool_call ----------
def test_salvage_truncated_write_file():
    raw = '{"tool_calls":[{"name":"write_file","arguments":{"path":"/tmp/a.html","content":"<html><body>lots of text that never closes'
    c, t, how = toolcall.parse(raw)
    assert c and c[0]["function"]["name"] == "write_file"
    args = json.loads(c[0]["function"]["arguments"])
    assert args["path"] == "/tmp/a.html"
    assert "html" in args["content"]


def test_bigfile_trigger():
    import bigfile
    assert bigfile.is_big_write("write_file", {"content": "x" * 2000}, "clean")
    assert not bigfile.is_big_write("write_file", {"content": "tiny"}, "clean")
    assert bigfile.is_big_write("write_file", {"content": "tiny"}, "salvaged")
    assert not bigfile.is_big_write("read_file", {"content": "x" * 2000}, "clean")


def test_funccall_style_parsed():
    raw = 'I call tools.\nwrite_file({"path":"a.html","content":"<html></html>"})\nbrowser_navigate({"url":"file:///a"})'
    c, t, how = toolcall.parse(raw)
    assert how == "funccall" and len(c) == 2
    assert c[0]["function"]["name"] == "write_file"
    assert c[1]["function"]["name"] == "browser_navigate"


def test_funccall_no_false_positive():
    c, t, how = toolcall.parse("Done. It printed 15 primes under 50.")
    assert c is None and how == "text"


# ---------- validator ----------
def test_valid_args_pass():
    ok, _ = validator.validate_tool_calls(
        [{"function": {"name": "read_file", "arguments": '{"path":"/x"}'}}], TOOLS)
    assert ok


def test_missing_required_arg_rejected():
    ok, _ = validator.validate_tool_calls(
        [{"function": {"name": "read_file", "arguments": "{}"}}], TOOLS)
    assert not ok


def test_unknown_tool_rejected():
    ok, _ = validator.validate_tool_calls(
        [{"function": {"name": "nope", "arguments": "{}"}}], TOOLS)
    assert not ok


def test_vote_consensus():
    ans = [
        '{"tool_calls":[{"name":"read_file","arguments":{"path":"/x"}}]}',
        '{"tool_calls":[{"name":"read_file","arguments":{"path":"/x"}}]}',
        'I think the answer is 42.',
    ]
    d = validator.vote(ans, TOOLS)
    assert d["kind"] == "tool_calls" and d["votes"] == 2


# ---------- BUG FIX: single-call-per-turn + no-hallucinated-completion ----------
def test_prompt_forbids_parallel_chaining():
    p = pb.build_prompt([{"role": "user", "content": "hi"}], TOOLS)
    assert "EXACTLY ONE tool per turn" in p
    assert "run in parallel" not in p


def test_prompt_has_anti_hallucination_clause():
    p = pb.build_prompt([{"role": "user", "content": "hi"}], TOOLS)
    assert "NO ability to act on your own" in p


def test_claims_done_detects_fake_completion():
    import api
    assert api._claims_done("Готово. Лендинг создан по пути /a/index.html")
    assert api._claims_done("File created at /tmp/a.html")
    assert not api._claims_done("Привет! Чем помочь?")
    assert not api._claims_done("15 простых чисел меньше 50")


def _gate_should_fire(messages):
    last = messages[-1].get("role") if messages else None
    tools = [m for m in messages if m.get("role") == "tool"]
    return last == "tool" and len(tools) >= 3


def test_gate_skips_on_fresh_user_request():
    msgs = [{"role": "tool"}, {"role": "tool"}, {"role": "tool"},
            {"role": "assistant"}, {"role": "user", "content": "fix it"}]
    assert _gate_should_fire(msgs) is False


def test_gate_fires_mid_loop():
    msgs = [{"role": "tool"}, {"role": "tool"}, {"role": "tool"}]
    assert _gate_should_fire(msgs) is True


# ---------- prompt builder ----------
def test_prompt_contains_tools_and_protocol():
    p = pb.build_prompt([{"role": "user", "content": "hi"}], TOOLS)
    assert "AVAILABLE TOOLS" in p and "read_file" in p
    assert "RESPONSE PROTOCOL" in p


# ---------- BUG FIX: 'deep' must not match 'deepseek' ----------
def _mode_for(model):
    return "deep" if ("-deep" in model or model.endswith("deep")) else "tool_loop"


def test_mode_routing_bugfix():
    assert _mode_for("deepseek-chat") == "tool_loop"
    assert _mode_for("deepseek-expert") == "tool_loop"
    assert _mode_for("deepseek-deep") == "deep"
    assert _mode_for("deep") == "deep"


# ---------- completion gate (anti-loop after tool results) ----------
def _has_tool_results(msgs):
    return any(m.get("role") == "tool" for m in msgs)


def _yes(v):
    v = v.strip().upper()
    return v.startswith("YES") or "YES" in v[:10]


def _gate_fires(msgs):
    return len([m for m in msgs if m.get("role") == "tool"]) >= 3


def test_completion_gate_trigger():
    assert _gate_fires([{"role": "user", "content": "x"}]) is False
    assert _gate_fires([{"role": "tool"}, {"role": "tool"}]) is False
    assert _gate_fires([{"role": "tool"}] * 3) is True


def test_completion_gate_yes_detect():
    assert _yes("YES") and _yes("yes done") and not _yes("NO")


def test_clean_text_strips_markers():
    dirty = "# ASSISTANT (called tools)\nTOOL RESULT (for call_123): {\"x\":1}\nReady, done."
    _, content, _ = toolcall.parse(dirty)
    assert "ASSISTANT" not in content
    assert "TOOL RESULT" not in content
    assert "Ready, done." in content


if __name__ == "__main__":
    # Allow running without pytest installed.
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = []
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as e:
            print("FAIL", fn.__name__, e)
            failed.append(fn.__name__)
    print(f"\n{len(fns) - len(failed)}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
